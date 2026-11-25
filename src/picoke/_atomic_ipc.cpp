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
#include <exception>
#include <sys/types.h>
#include <sys/wait.h>
#include <vector>
#include <mutex>
#include <signal.h>
#include <algorithm>
#include <pthread.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace py = pybind11;

#if !defined(__linux__) || !defined(__x86_64__)
#error "picoke AtomicSharedByteRing requires Linux x86-64"
#endif

namespace {
[[noreturn]] void throw_oserror(int error,const char*){errno=error;PyErr_SetFromErrno(PyExc_OSError);throw py::error_already_set();}
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
#ifdef PICOKE_IPC_TEST_HOOKS
    std::string failpoint;
    int hook_fd=-1;
    void hit(const char* id){if(failpoint!=id)return;std::string msg=std::string(id)+"\n";(void)::write(hook_fd,msg.data(),msg.size());for(;;)pause();}
#else
    void hit(const char*){}
#endif

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
            hit("before_linkat");
            const std::string fd_path = "/proc/self/fd/" + std::to_string(fd);
            if (linkat(AT_FDCWD, fd_path.c_str(), AT_FDCWD, ("/dev/shm" + name).c_str(), AT_SYMLINK_FOLLOW) < 0)
                throw std::system_error(errno, std::generic_category(), "publish shared ring");
            hit("after_linkat");
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
        hit("producer_after_length");
#ifdef PICOKE_IPC_TEST_HOOKS
        if (n) { size_t part=n>1?static_cast<size_t>(n)/2:static_cast<size_t>(n); std::memcpy(static_cast<char*>(base)+slot+sizeof len,data,part); hit("producer_after_partial_payload"); if(part<static_cast<size_t>(n))std::memcpy(static_cast<char*>(base)+slot+sizeof len+part,data+part,static_cast<size_t>(n)-part); }
#else
        if (n) std::memcpy(static_cast<char*>(base) + slot + sizeof len, data, static_cast<size_t>(n));
#endif
        hit("before_write_seq");
        h->write_seq.store(w + 1, std::memory_order_release);
        hit("after_write_seq");
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
        hit("before_read_seq");
        h->read_seq.store(r + 1, std::memory_order_release);
        hit("after_read_seq");
        return result;
    }
    void close() {
        if (pid != getpid()) throw std::runtime_error("atomic ring cannot be used after fork");
        if (!closed) release();
    }
#ifdef PICOKE_IPC_TEST_HOOKS
    void set_failpoint(const std::string&p,int f){check();failpoint=p;hook_fd=f;}
    void clear_failpoint(){check();failpoint.clear();hook_fd=-1;}
#endif
};

std::string validate_name(const std::string& name) {
    if (name.size() < 2 || name[0] != '/' || name.find('/', 1) != std::string::npos || name.find('\0') != std::string::npos)
        throw py::value_error("name must be a single-component POSIX shared-memory name");
    return name;
}
#ifdef PICOKE_IPC_TEST_HOOKS
std::string next_create_failpoint;
int next_create_hook_fd=-1;
void set_next_create_failpoint(const std::string& name,int fd){next_create_failpoint=name;next_create_hook_fd=fd;}
#endif
std::shared_ptr<Ring> make_ring(const std::string& name, uint64_t cap, uint64_t max) {
    auto n = validate_name(name);
    auto r = std::make_shared<Ring>();
#ifdef PICOKE_IPC_TEST_HOOKS
    r->failpoint=next_create_failpoint;r->hook_fd=next_create_hook_fd;next_create_failpoint.clear();next_create_hook_fd=-1;
#endif
    try { r->initialize(n, cap, max, true); }
    catch (...) { r->release(); throw; }
    return r;
}
std::shared_ptr<Ring> attach_ring(const std::string& name) {
    auto r = std::make_shared<Ring>(); r->initialize(validate_name(name), 0, 0, false); return r;
}
constexpr char REC_MAGIC[8] = {'P','I','C','O','R','C','V','1'};
struct alignas(64) RecoverHeader {
    std::atomic<uint64_t> write_seq, read_seq, capacity, max_item;
    std::atomic<uint32_t> initialized;
    uint32_t version;
    char magic[8];
    alignas(64) std::atomic<uint32_t> producer_dirty;
    alignas(64) std::atomic<uint32_t> consumer_dirty;
};
class RecoverableRing;
void register_recoverable(RecoverableRing*);
void unregister_recoverable(RecoverableRing*);
struct RoleBusyNative : std::runtime_error { using std::runtime_error::runtime_error; };
struct RecoveryRequiredNative : std::runtime_error { using std::runtime_error::runtime_error; };
class RecoverableRing {
public:
    int fd=-1, rolefd[2]={-1,-1}; void* base=MAP_FAILED; size_t length=0;
    uint64_t capacity=0,max_item=0; pid_t pid=0; bool closed=true,automatic=false,claimed[2]={false,false},role_failed[2]={false,false};
#ifdef PICOKE_IPC_TEST_HOOKS
    std::string failpoint; int hook_fd=-1;
    void hit(const char* id){if(failpoint!=id)return;std::string msg=std::string(id)+"\n";(void)::write(hook_fd,msg.data(),msg.size());for(;;)pause();}
#else
    void hit(const char*){}
#endif
    ~RecoverableRing(){unregister_recoverable(this);release_all();}
    static size_t ring_size(uint64_t c,uint64_t m){
        if(!c||(c&(c-1))||m>PY_SSIZE_T_MAX||m>SIZE_MAX-8||c>(SIZE_MAX-sizeof(RecoverHeader))/(8+size_t(m)))throw py::value_error("invalid recoverable ring bounds");
        size_t z=sizeof(RecoverHeader)+size_t(c)*(8+size_t(m));if(z>size_t(std::numeric_limits<off_t>::max()))throw py::value_error("ring too large");return z;
    }
    void release_all() noexcept {for(int i=0;i<2;i++)if(rolefd[i]>=0){::close(rolefd[i]);rolefd[i]=-1;}if(base!=MAP_FAILED){munmap(base,length);base=MAP_FAILED;}if(fd>=0){::close(fd);fd=-1;}closed=true;}
    void initialize(const std::string&name,uint64_t c,uint64_t m,bool create,bool auto_mode){
        automatic=auto_mode;size_t expected=create?ring_size(c,m):0;
        fd=create?open("/dev/shm",O_TMPFILE|O_RDWR|O_CLOEXEC,0600):shm_open(name.c_str(),O_RDWR|O_CLOEXEC,0);
        if(fd<0)throw_oserror(errno,"open recoverable ring");
        if(create&&ftruncate(fd,off_t(expected))<0)throw_oserror(errno,"ftruncate");
        struct stat st{};if(fstat(fd,&st)<0)throw_oserror(errno,"fstat");
        if(create?size_t(st.st_size)!=expected:st.st_size<off_t(sizeof(RecoverHeader)))throw py::value_error("invalid recoverable ring size");
        length=size_t(st.st_size);base=mmap(nullptr,length,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
        if(base==MAP_FAILED)throw_oserror(errno,"mmap");
        auto*h=static_cast<RecoverHeader*>(base);
        if(create){
            new(&h->write_seq)std::atomic<uint64_t>(0);new(&h->read_seq)std::atomic<uint64_t>(0);new(&h->capacity)std::atomic<uint64_t>(c);new(&h->max_item)std::atomic<uint64_t>(m);new(&h->initialized)std::atomic<uint32_t>(0);
            new(&h->producer_dirty)std::atomic<uint32_t>(0);new(&h->consumer_dirty)std::atomic<uint32_t>(0);h->version=1;std::memcpy(h->magic,REC_MAGIC,8);h->initialized.store(1,std::memory_order_release);capacity=c;max_item=m;
            hit("before_linkat");auto p="/proc/self/fd/"+std::to_string(fd);
            if(linkat(AT_FDCWD,p.c_str(),AT_FDCWD,("/dev/shm"+name).c_str(),AT_SYMLINK_FOLLOW)<0)throw_oserror(errno,"publish ring");
            hit("after_linkat");
        }else validate();
        pid=getpid();closed=false;
    }
    void stable_check(){auto*h=static_cast<RecoverHeader*>(base);constexpr unsigned attempts=1000;for(unsigned i=0;i<attempts;i++){uint64_t r1=h->read_seq.load(std::memory_order_acquire),w=h->write_seq.load(std::memory_order_acquire),r2=h->read_seq.load(std::memory_order_acquire);if(r1==r2){if(w-r2>capacity)throw std::runtime_error("corrupt recoverable sequence state");return;}}throw std::runtime_error("could not obtain stable recoverable sequence snapshot");}
    void validate(){auto*h=static_cast<RecoverHeader*>(base);if(h->initialized.load(std::memory_order_acquire)!=1||h->version!=1||memcmp(h->magic,REC_MAGIC,8))throw py::value_error("invalid recoverable ring header");capacity=h->capacity.load();max_item=h->max_item.load();if(ring_size(capacity,max_item)!=length)throw py::value_error("invalid recoverable ring layout");stable_check();}
    void check()const{if(pid!=getpid())throw std::runtime_error("ring cannot be used after fork");if(closed)throw py::value_error("ring is closed");}
    int role(const std::string&r){if(r=="producer")return 0;if(r=="consumer")return 1;throw py::value_error("role must be producer or consumer");}
    void acquire(int i,bool recovering){check();if(claimed[i])throw std::runtime_error("role already claimed");int d=open(("/proc/self/fd/"+std::to_string(fd)).c_str(),O_RDWR|O_CLOEXEC);if(d<0)throw_oserror(errno,"open role descriptor");
        struct flock fl{};fl.l_type=F_WRLCK;fl.l_whence=SEEK_SET;fl.l_start=i;fl.l_len=1;if(fcntl(d,F_OFD_SETLK,&fl)<0){int e=errno;::close(d);if(e==EAGAIN||e==EACCES)throw RoleBusyNative("role is already claimed");throw_oserror(e,"OFD lock");}
        rolefd[i]=d;auto*h=static_cast<RecoverHeader*>(base);auto&dirty=i?h->consumer_dirty:h->producer_dirty;
        if(!recovering&&dirty.load(std::memory_order_acquire)&&!automatic){::close(d);rolefd[i]=-1;throw RecoveryRequiredNative("role requires recovery");}
        dirty.store(1,std::memory_order_release);hit(i?"consumer_dirty":"producer_dirty");
        try{validate();}catch(...){::close(d);rolefd[i]=-1;throw;}claimed[i]=true;role_failed[i]=false;
    }
    void claim(const std::string&r){acquire(role(r),false);}
    void recover(const std::string&r){if(automatic)throw py::value_error("automatic ring has no recover method");acquire(role(r),true);}
    void abandon_role(int i)noexcept{if(rolefd[i]>=0){::close(rolefd[i]);rolefd[i]=-1;}claimed[i]=false;}
    void release_role(const std::string&r){int i=role(r);check();if(!claimed[i])throw std::runtime_error("role not claimed");if(role_failed[i]){abandon_role(i);throw std::runtime_error("role operation failed; recovery required");}try{stable_check();}catch(...){role_failed[i]=true;abandon_role(i);throw;}auto*h=static_cast<RecoverHeader*>(base);(i?h->consumer_dirty:h->producer_dirty).store(0,std::memory_order_release);hit("before_unlock");abandon_role(i);}
    bool push(py::bytes item){
        check();if(!claimed[0])throw std::runtime_error("producer role not claimed");if(role_failed[0])throw std::runtime_error("producer role failed; recovery required");
        char*d;Py_ssize_t n;if(PyBytes_AsStringAndSize(item.ptr(),&d,&n)<0)throw py::error_already_set();
        if(uint64_t(n)>max_item)throw py::value_error("item exceeds max_item_size");
        auto*h=static_cast<RecoverHeader*>(base);uint64_t w=h->write_seq.load(std::memory_order_relaxed),r=h->read_seq.load(std::memory_order_acquire);
        if(w-r>capacity){role_failed[0]=true;throw std::runtime_error("corrupt recoverable sequence state");}if(w-r==capacity)return false;
        size_t s=sizeof(RecoverHeader)+size_t(w&(capacity-1))*(8+size_t(max_item));uint64_t len=n;memcpy(static_cast<char*>(base)+s,&len,8);hit("producer_after_length");
        if(n){
#ifdef PICOKE_IPC_TEST_HOOKS
            size_t part=n>1?size_t(n)/2:size_t(n);memcpy(static_cast<char*>(base)+s+8,d,part);hit("producer_after_partial_payload");if(part<size_t(n))memcpy(static_cast<char*>(base)+s+8+part,d+part,size_t(n)-part);
#else
            memcpy(static_cast<char*>(base)+s+8,d,size_t(n));
#endif
        }
        hit("before_write_seq");h->write_seq.store(w+1,std::memory_order_release);hit("after_write_seq");return true;
    }
    py::object pop(){
        check();if(!claimed[1])throw std::runtime_error("consumer role not claimed");if(role_failed[1])throw std::runtime_error("consumer role failed; recovery required");
        auto*h=static_cast<RecoverHeader*>(base);uint64_t r=h->read_seq.load(std::memory_order_relaxed),w=h->write_seq.load(std::memory_order_acquire);
        if(w-r>capacity){role_failed[1]=true;throw std::runtime_error("corrupt recoverable sequence state");}if(w==r)return py::none();
        size_t s=sizeof(RecoverHeader)+size_t(r&(capacity-1))*(8+size_t(max_item));uint64_t n;memcpy(&n,static_cast<char*>(base)+s,8);hit("consumer_after_length");
        if(n>max_item||n>PY_SSIZE_T_MAX){role_failed[1]=true;throw std::runtime_error("corrupt recoverable slot");}
        py::bytes out;
        if(n){auto*obj=PyBytes_FromStringAndSize(nullptr,Py_ssize_t(n));if(!obj){role_failed[1]=true;throw py::error_already_set();}out=py::reinterpret_steal<py::bytes>(obj);char*dst=PyBytes_AS_STRING(out.ptr());
#ifdef PICOKE_IPC_TEST_HOOKS
            size_t part=n>1?size_t(n)/2:size_t(n);memcpy(dst,static_cast<char*>(base)+s+8,part);hit("consumer_after_partial_payload");if(part<size_t(n))memcpy(dst+part,static_cast<char*>(base)+s+8+part,size_t(n)-part);
#else
            memcpy(dst,static_cast<char*>(base)+s+8,size_t(n));
#endif
        }else{try{out=py::bytes("");}catch(...){role_failed[1]=true;throw;}hit("consumer_after_partial_payload");}
        hit("before_read_seq");h->read_seq.store(r+1,std::memory_order_release);hit("after_read_seq");return out;
    }
    void close(){if(pid!=getpid())throw std::runtime_error("ring cannot be used after fork");if(closed)return;std::exception_ptr failure;for(int i=0;i<2;i++)if(claimed[i]){if(role_failed[i]){abandon_role(i);continue;}try{release_role(i?"consumer":"producer");}catch(...){if(!failure)failure=std::current_exception();abandon_role(i);}}release_all();if(failure)std::rethrow_exception(failure);}
#ifdef PICOKE_IPC_TEST_HOOKS
    void set_failpoint(const std::string&p,int f){check();failpoint=p;hook_fd=f;}void clear_failpoint(){check();failpoint.clear();hook_fd=-1;}
#endif
};

std::vector<RecoverableRing*> live_recoverables;
bool atfork_registered=false;
void fork_child_cleanup(){for(auto*r:live_recoverables)for(int i=0;i<2;i++)if(r->rolefd[i]>=0){::close(r->rolefd[i]);r->rolefd[i]=-1;}}
void register_recoverable(RecoverableRing*r){live_recoverables.push_back(r);if(!atfork_registered){int e=pthread_atfork(nullptr,nullptr,fork_child_cleanup);if(e)throw std::system_error(e,std::generic_category(),"pthread_atfork");atfork_registered=true;}}
void unregister_recoverable(RecoverableRing*r){auto it=std::find(live_recoverables.begin(),live_recoverables.end(),r);if(it!=live_recoverables.end())live_recoverables.erase(it);}
std::shared_ptr<RecoverableRing> make_recoverable(const std::string&n,uint64_t c,uint64_t m,bool a){auto r=std::make_shared<RecoverableRing>();
#ifdef PICOKE_IPC_TEST_HOOKS
    r->failpoint=next_create_failpoint;r->hook_fd=next_create_hook_fd;next_create_failpoint.clear();next_create_hook_fd=-1;
#endif
    try{r->initialize(validate_name(n),c,m,true,a);register_recoverable(r.get());}catch(...){r->release_all();throw;}return r;}
std::shared_ptr<RecoverableRing> attach_recoverable(const std::string&n,bool a){auto r=std::make_shared<RecoverableRing>();try{r->initialize(validate_name(n),0,0,false,a);register_recoverable(r.get());}catch(...){r->release_all();throw;}return r;}
}

PYBIND11_MODULE(_atomic_ipc_native, m) {
    py::class_<Ring, std::shared_ptr<Ring>>(m, "Ring")
        .def("check_open", &Ring::check).def("try_push", &Ring::push).def("try_pop", &Ring::pop)
        .def("close", &Ring::close)
#ifdef PICOKE_IPC_TEST_HOOKS
        .def("set_failpoint",&Ring::set_failpoint).def("clear_failpoint",&Ring::clear_failpoint)
#endif
        .def_property_readonly("max_item_size", [](const Ring& ring) { return ring.max_item; });
    py::register_exception<RoleBusyNative>(m,"RoleBusyError",PyExc_RuntimeError);
    py::register_exception<RecoveryRequiredNative>(m,"RecoveryRequiredError",PyExc_RuntimeError);
    py::class_<RecoverableRing,std::shared_ptr<RecoverableRing>>(m,"RecoverableRing")
        .def("check_open",&RecoverableRing::check).def("claim",&RecoverableRing::claim)
        .def("release",&RecoverableRing::release_role).def("recover",&RecoverableRing::recover)
        .def("try_push",&RecoverableRing::push).def("try_pop",&RecoverableRing::pop).def("close",&RecoverableRing::close)
#ifdef PICOKE_IPC_TEST_HOOKS
        .def("set_failpoint",&RecoverableRing::set_failpoint).def("clear_failpoint",&RecoverableRing::clear_failpoint)
#endif
        .def_property_readonly("max_item_size",[](const RecoverableRing&r){return r.max_item;});
    m.def("create",&make_ring);m.def("attach",&attach_ring);
    m.def("create_recoverable",&make_recoverable,py::arg("name"),py::arg("capacity"),py::arg("max_item_size"),py::arg("automatic")=false);
    m.def("attach_recoverable",&attach_recoverable,py::arg("name"),py::arg("automatic")=false);
#ifdef PICOKE_IPC_TEST_HOOKS
    m.def("set_next_create_failpoint",&set_next_create_failpoint,py::arg("name"),py::arg("fd"));
#endif
#ifdef PICOKE_IPC_TEST_HOOKS
    m.attr("TEST_HOOKS")=true;
#else
    m.attr("TEST_HOOKS")=false;
#endif
    m.def("unlink",[](const std::string&name){auto n=validate_name(name);if(shm_unlink(n.c_str())<0&&errno!=ENOENT){PyErr_SetFromErrno(PyExc_OSError);throw py::error_already_set();}});
}

