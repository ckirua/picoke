#define _GNU_SOURCE
#include <pybind11/pybind11.h>
#include <atomic>
#include <system_error>
#include <cstddef>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <new>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

namespace py = pybind11;

#if !defined(__linux__) || !defined(__x86_64__)
#error "picoke AtomicSharedByteRing requires Linux x86-64"
#endif

namespace {
constexpr char MAGIC[8] = {'P','I','C','O','A','T','M','1'};
constexpr uint32_t VERSION = 1;
struct alignas(64) Header {
    std::atomic<uint64_t> write_seq;
    alignas(64) std::atomic<uint64_t> read_seq;
    std::atomic<uint64_t> capacity;
    std::atomic<uint64_t> max_item;
    std::atomic<uint32_t> initialized;
    uint32_t version;
    char magic[8];
};
static_assert(std::atomic<uint64_t>::is_always_lock_free, "requires lock-free 64-bit atomics");
static_assert(alignof(Header) >= alignof(std::atomic<uint64_t>), "atomic header alignment");

size_t checked_length(uint64_t capacity, uint64_t max_item) {
    const size_t overhead = sizeof(uint64_t);
    if (!capacity || (capacity & (capacity - 1)) || max_item > static_cast<uint64_t>(PY_SSIZE_T_MAX) ||
        max_item > SIZE_MAX - overhead || capacity > (SIZE_MAX - sizeof(Header)) / (overhead + static_cast<size_t>(max_item)))
        throw py::value_error("invalid atomic ring bounds");
    size_t length = sizeof(Header) + static_cast<size_t>(capacity) * (overhead + static_cast<size_t>(max_item));
    if (length > static_cast<size_t>(std::numeric_limits<off_t>::max())) throw py::value_error("ring too large");
    return length;
}

class Ring {
public:
    int fd = -1;
    void* base = MAP_FAILED;
    size_t length = 0;
    uint64_t capacity = 0, max_item = 0;
    pid_t pid = 0;
    bool closed = true;

    Ring() = default;
    Ring(const Ring&) = delete;
    Ring& operator=(const Ring&) = delete;
    ~Ring() { release(); }
    void release() noexcept {
        if (base != MAP_FAILED) { munmap(base, length); base = MAP_FAILED; }
        if (fd >= 0) { ::close(fd); fd = -1; }
        closed = true;
    }
    void initialize(const std::string& name, uint64_t cap, uint64_t max, bool create) {
        const size_t expected = create ? checked_length(cap, max) : 0;
        fd = create ? open("/dev/shm", O_TMPFILE | O_RDWR | O_CLOEXEC, 0600)
                    : shm_open(name.c_str(), O_RDWR | O_CLOEXEC, 0);
        if (fd < 0) { PyErr_SetFromErrno(PyExc_OSError); throw py::error_already_set(); }
        if (create && ftruncate(fd, static_cast<off_t>(expected)) < 0) throw std::system_error(errno, std::generic_category(), "ftruncate");
        struct stat st{};
        if (fstat(fd, &st) < 0) throw std::system_error(errno, std::generic_category(), "fstat");
        if (create) {
            if (static_cast<uint64_t>(st.st_size) != expected) throw py::value_error("invalid atomic ring size");
            length = expected;
        } else {
            if (st.st_size < static_cast<off_t>(sizeof(Header)) || static_cast<uintmax_t>(st.st_size) > SIZE_MAX)
                throw py::value_error("invalid atomic ring segment");
            length = static_cast<size_t>(st.st_size);
        }
        base = mmap(nullptr, length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (base == MAP_FAILED) throw std::system_error(errno, std::generic_category(), "mmap");
        auto* h = static_cast<Header*>(base);
        if (create) {
            new (&h->write_seq) std::atomic<uint64_t>(0);
            new (&h->read_seq) std::atomic<uint64_t>(0);
            new (&h->capacity) std::atomic<uint64_t>(cap);
            new (&h->max_item) std::atomic<uint64_t>(max);
            new (&h->initialized) std::atomic<uint32_t>(0);
            h->version = VERSION;
            std::memcpy(h->magic, MAGIC, sizeof MAGIC);
            h->initialized.store(1, std::memory_order_release);
            capacity = cap; max_item = max;
            // Publish only after every header field and the layout are ready.
            const std::string fd_path = "/proc/self/fd/" + std::to_string(fd);
            if (linkat(AT_FDCWD, fd_path.c_str(), AT_FDCWD, ("/dev/shm" + name).c_str(), AT_SYMLINK_FOLLOW) < 0)
                throw std::system_error(errno, std::generic_category(), "publish shared ring");
        } else {
            if (h->initialized.load(std::memory_order_acquire) != 1 || std::memcmp(h->magic, MAGIC, sizeof MAGIC) || h->version != VERSION)
                throw py::value_error("invalid or uninitialized atomic ring segment");
            capacity = h->capacity.load(std::memory_order_relaxed);
            max_item = h->max_item.load(std::memory_order_relaxed);
            if (checked_length(capacity, max_item) != length) throw py::value_error("invalid atomic ring layout");
        }
        pid = getpid(); closed = false;
    }
    void check() const {
        if (pid != getpid()) throw std::runtime_error("atomic ring cannot be used after fork");
        if (closed) throw py::value_error("atomic ring is closed");
    }
    bool push(py::bytes item) {
        check();
        char* data; Py_ssize_t n;
        if (PyBytes_AsStringAndSize(item.ptr(), &data, &n) < 0) throw py::error_already_set();
        if (static_cast<uint64_t>(n) > max_item) throw py::value_error("item exceeds max_item_size");
        auto* h = static_cast<Header*>(base);
        uint64_t w = h->write_seq.load(std::memory_order_relaxed);
        uint64_t r = h->read_seq.load(std::memory_order_acquire);
        uint64_t used = w - r;
        if (used > capacity) throw std::runtime_error("corrupt atomic ring sequence state");
        if (used == capacity) return false;
        size_t slot = sizeof(Header) + static_cast<size_t>(w & (capacity - 1)) * (sizeof(uint64_t) + static_cast<size_t>(max_item));
        uint64_t len = static_cast<uint64_t>(n);
        std::memcpy(static_cast<char*>(base) + slot, &len, sizeof len);
        if (n) std::memcpy(static_cast<char*>(base) + slot + sizeof len, data, static_cast<size_t>(n));
        h->write_seq.store(w + 1, std::memory_order_release);
        return true;
    }
    py::object pop() {
        check();
        auto* h = static_cast<Header*>(base);
        uint64_t r = h->read_seq.load(std::memory_order_relaxed);
        uint64_t w = h->write_seq.load(std::memory_order_acquire);
        uint64_t used = w - r;
        if (used > capacity) throw std::runtime_error("corrupt atomic ring sequence state");
        if (!used) return py::none();
        size_t slot = sizeof(Header) + static_cast<size_t>(r & (capacity - 1)) * (sizeof(uint64_t) + static_cast<size_t>(max_item));
        uint64_t len;
        std::memcpy(&len, static_cast<char*>(base) + slot, sizeof len);
        if (len > max_item || len > static_cast<uint64_t>(PY_SSIZE_T_MAX)) throw std::runtime_error("corrupt atomic ring slot");
        py::bytes result(static_cast<char*>(base) + slot + sizeof len, static_cast<Py_ssize_t>(len));
        h->read_seq.store(r + 1, std::memory_order_release);
        return result;
    }
    void close() {
        if (pid != getpid()) throw std::runtime_error("atomic ring cannot be used after fork");
        if (!closed) release();
    }
};

std::string validate_name(const std::string& name) {
    if (name.size() < 2 || name[0] != '/' || name.find('/', 1) != std::string::npos || name.find('\0') != std::string::npos)
        throw py::value_error("name must be a single-component POSIX shared-memory name");
    return name;
}
std::shared_ptr<Ring> make_ring(const std::string& name, uint64_t cap, uint64_t max) {
    auto n = validate_name(name);
    auto r = std::make_shared<Ring>();
    try { r->initialize(n, cap, max, true); }
    catch (...) { r->release(); throw; }
    return r;
}
std::shared_ptr<Ring> attach_ring(const std::string& name) {
    auto r = std::make_shared<Ring>(); r->initialize(validate_name(name), 0, 0, false); return r;
}
}

PYBIND11_MODULE(_atomic_ipc_native, m) {
    py::class_<Ring, std::shared_ptr<Ring>>(m, "Ring")
        .def("check_open", &Ring::check)
        .def("try_push", &Ring::push)
        .def("try_pop", &Ring::pop)
        .def("close", &Ring::close)
        .def_property_readonly("max_item_size", [](const Ring& ring) { return ring.max_item; });
    m.def("create", &make_ring);
    m.def("attach", &attach_ring);
    m.def("unlink", [](const std::string& name) {
        auto n = validate_name(name);
        if (shm_unlink(n.c_str()) < 0 && errno != ENOENT) { PyErr_SetFromErrno(PyExc_OSError); throw py::error_already_set(); }
    });
}

