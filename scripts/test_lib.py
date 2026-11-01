import ctypes
lib = ctypes.CDLL("/home/chethan/snap/code/257/.local/share/com.example.pai_mobile/plugins/camera/1.0.0/linux/libpai_camera.so")

class KV(ctypes.Structure):
    _fields_ = [("key", ctypes.c_char_p), ("value", ctypes.c_char_p)]

class KVList(ctypes.Structure):
    _fields_ = [("items", ctypes.POINTER(KV)), ("count", ctypes.c_size_t)]

class Module(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_int),
        ("name", ctypes.CFUNCTYPE(ctypes.c_char_p)),
        # ...other fn ptrs, not needed for this check
    ]

lib.pai_plugin_module_create.restype = ctypes.POINTER(Module)
mod = lib.pai_plugin_module_create()
print("plugin reports name =", mod.contents.name())