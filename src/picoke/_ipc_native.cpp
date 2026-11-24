#include <pybind11/pybind11.h>
#include <Python.h>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace py = pybind11;

namespace {
constexpr std::size_t HEADER_SIZE = 48;  // <8sQQQQQ
constexpr std::size_t LENGTH_SIZE = 8;

std::uint64_t load_u64(const char* p) {
    std::uint64_t value;
    std::memcpy(&value, p, sizeof(value));
    #if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    value = __builtin_bswap64(value);
    #endif
    return value;
}
void store_u64(char* p, std::uint64_t value) {
    #if __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    value = __builtin_bswap64(value);
    #endif
    std::memcpy(p, &value, sizeof(value));
}

struct WritableBuffer {
    Py_buffer view{};
    explicit WritableBuffer(py::handle obj) {
        if (PyObject_GetBuffer(obj.ptr(), &view, PyBUF_WRITABLE | PyBUF_C_CONTIGUOUS) < 0)
            throw py::error_already_set();
        if (view.len < 0) {
            PyBuffer_Release(&view);
            throw py::value_error("invalid mapping buffer length");
        }
    }
    ~WritableBuffer() { PyBuffer_Release(&view); }
};

void check_layout(char* base, std::size_t size, std::uint64_t cap,
                  std::uint64_t max_size, std::uint64_t& head,
                  std::uint64_t& tail, std::uint64_t& count) {
    if (size < HEADER_SIZE) throw py::value_error("invalid SharedByteRing segment");
    if (std::memcmp(base, "PICORNG1", 8) != 0 || load_u64(base + 8) != cap ||
        load_u64(base + 16) != max_size)
        throw py::value_error("invalid SharedByteRing segment");
    head = load_u64(base + 24); tail = load_u64(base + 32); count = load_u64(base + 40);
    if (max_size > std::numeric_limits<std::size_t>::max() - LENGTH_SIZE ||
        cap == 0 || head >= cap || tail >= cap || count > cap ||
        cap > (std::numeric_limits<std::size_t>::max() - HEADER_SIZE) / (LENGTH_SIZE + max_size) ||
        HEADER_SIZE + cap * (LENGTH_SIZE + max_size) != size)
        throw py::value_error("invalid SharedByteRing segment");
}
}

// Caller must hold SharedByteRing._guard and flock(LOCK_EX), in that order.
bool try_push(py::handle mapping, py::handle item, std::uint64_t cap,
              std::uint64_t max_size) {
    if (!PyBytes_CheckExact(item.ptr())) throw py::type_error("item must be exact bytes");
    const Py_ssize_t n = PyBytes_GET_SIZE(item.ptr());
    if (n < 0 || static_cast<std::uint64_t>(n) > max_size)
        throw py::value_error("item exceeds max_item_size");
    WritableBuffer buffer(mapping);
    auto* base = static_cast<char*>(buffer.view.buf);
    std::uint64_t head, tail, count;
    check_layout(base, static_cast<std::size_t>(buffer.view.len), cap, max_size,
                 head, tail, count);
    if (count == cap) return false;
    const std::size_t slot = HEADER_SIZE + static_cast<std::size_t>(tail) * (LENGTH_SIZE + static_cast<std::size_t>(max_size));
    store_u64(base + slot, static_cast<std::uint64_t>(n));
    if (n) std::memcpy(base + slot + LENGTH_SIZE, PyBytes_AS_STRING(item.ptr()), static_cast<std::size_t>(n));
    tail = (tail + 1) % cap;
    store_u64(base + 32, tail);
    store_u64(base + 40, count + 1);
    return true;
}

py::object try_pop(py::handle mapping, std::uint64_t cap, std::uint64_t max_size) {
    WritableBuffer buffer(mapping);
    auto* base = static_cast<char*>(buffer.view.buf);
    std::uint64_t head, tail, count;
    check_layout(base, static_cast<std::size_t>(buffer.view.len), cap, max_size,
                 head, tail, count);
    if (count == 0) return py::none();
    const std::size_t slot = HEADER_SIZE + static_cast<std::size_t>(head) * (LENGTH_SIZE + static_cast<std::size_t>(max_size));
    const std::uint64_t length = load_u64(base + slot);
    if (length > max_size) throw std::runtime_error("corrupt SharedByteRing slot");
    const std::size_t start = slot + LENGTH_SIZE;
    if (start > static_cast<std::size_t>(buffer.view.len) ||
        length > static_cast<std::size_t>(buffer.view.len) - start)
        throw std::runtime_error("corrupt SharedByteRing slot");
    py::object result = py::reinterpret_steal<py::object>(
        PyBytes_FromStringAndSize(base + start, static_cast<Py_ssize_t>(length)));
    if (!result) throw py::error_already_set();
    store_u64(base + slot, 0);
    head = (head + 1) % cap;
    store_u64(base + 24, head);
    store_u64(base + 40, count - 1);
    return result;
}

PYBIND11_MODULE(_ipc_native, m) {
    m.doc() = "PICORNG1 slot helpers; the caller owns synchronization.";
    m.def("try_push", &try_push, py::arg("mapping"), py::arg("item"), py::arg("capacity"), py::arg("max_item_size"));
    m.def("try_pop", &try_pop, py::arg("mapping"), py::arg("capacity"), py::arg("max_item_size"));
}
