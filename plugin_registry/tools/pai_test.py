#!/usr/bin/env python3
"""
Invoke a pai plugin .so directly via the C ABI. Mirrors what the Python
orchestrator loader does, so it's the fastest way to exercise capability
paths (including error paths) without booting the whole agent.
"""
import argparse
import ctypes
import json
import sys
from pathlib import Path

PAI_PLUGIN_ABI_VERSION = 1


class PaiKv(ctypes.Structure):
    _fields_ = [("key", ctypes.c_char_p), ("value", ctypes.c_char_p)]


class PaiKvList(ctypes.Structure):
    _fields_ = [
        ("items", ctypes.POINTER(PaiKv)),
        ("count", ctypes.c_size_t),
    ]


def build_module_struct():
    """Forward-declare pai_plugin_module_t with the right function-pointer types."""
    class PaiPluginModule(ctypes.Structure):
        pass

    PaiPluginModule._fields_ = [
        ("abi_version", ctypes.c_int),
        ("name", ctypes.CFUNCTYPE(ctypes.c_char_p)),
        ("capabilities", ctypes.CFUNCTYPE(
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_size_t))),
        ("permissions", ctypes.CFUNCTYPE(
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_size_t))),
        ("invoke", ctypes.CFUNCTYPE(
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.POINTER(PaiKvList),
            ctypes.POINTER(PaiKvList),
            ctypes.POINTER(ctypes.c_char_p))),
        ("request_permissions", ctypes.CFUNCTYPE(
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.c_size_t,
            ctypes.POINTER(PaiKvList),
            ctypes.POINTER(ctypes.c_char_p))),
        ("free_kv_list", ctypes.CFUNCTYPE(
            None, ctypes.POINTER(PaiKvList))),
        ("free_string", ctypes.CFUNCTYPE(None, ctypes.c_char_p)),
    ]
    return PaiPluginModule


PaiPluginModule = build_module_struct()


def make_kv_list(d: dict):
    """Build a PaiKvList from a dict. Returns (list, backing_array) so the
    array stays alive until after invoke() returns."""
    n = len(d)
    arr = (PaiKv * n)()
    for i, (k, v) in enumerate(d.items()):
        arr[i].key = str(k).encode()
        arr[i].value = str(v).encode()
    lst = PaiKvList()
    lst.items = ctypes.cast(arr, ctypes.POINTER(PaiKv))
    lst.count = n
    return lst, arr


def read_kv_list(lst: PaiKvList) -> dict:
    out = {}
    if not lst.items:
        return out
    for i in range(lst.count):
        k = lst.items[i].key or b""
        v = lst.items[i].value or b""
        out[k.decode(errors="replace")] = v.decode(errors="replace")
    return out


def load_plugin(so_path: Path):
    lib = ctypes.CDLL(str(so_path))

    lib.pai_plugin_module_create.restype = ctypes.POINTER(PaiPluginModule)
    lib.pai_plugin_module_create.argtypes = []
    lib.pai_plugin_module_destroy.argtypes = [ctypes.POINTER(PaiPluginModule)]

    module = lib.pai_plugin_module_create()
    if not module:
        raise RuntimeError(f"pai_plugin_module_create returned NULL for {so_path}")

    if module.contents.abi_version != PAI_PLUGIN_ABI_VERSION:
        raise RuntimeError(
            f"ABI mismatch in {so_path}: plugin has "
            f"{module.contents.abi_version}, host expects {PAI_PLUGIN_ABI_VERSION}")

    return lib, module


def describe(lib, module) -> dict:
    n = ctypes.c_size_t()
    caps_ptr = module.contents.capabilities(ctypes.byref(n))
    caps = [caps_ptr[i].decode() for i in range(n.value)] if caps_ptr else []

    n2 = ctypes.c_size_t()
    perms_ptr = module.contents.permissions(ctypes.byref(n2))
    perms = [perms_ptr[i].decode() for i in range(n2.value)] if perms_ptr else []

    return {
        "name": module.contents.name().decode(),
        "abi_version": module.contents.abi_version,
        "capabilities": caps,
        "permissions": perms,
    }


def invoke(lib, module, capability: str, params: dict) -> dict:
    params_list, params_backing = make_kv_list(params)
    out = PaiKvList()
    err = ctypes.c_char_p()

    rc = module.contents.invoke(
        capability.encode(),
        ctypes.byref(params_list),
        ctypes.byref(out),
        ctypes.byref(err),
    )

    # Copy out BEFORE freeing — free_kv_list nulls the pointer.
    try:
        result = read_kv_list(out)
    finally:
        module.contents.free_kv_list(ctypes.byref(out))

    # Same for err — read then free the C string.
    error_text = err.value.decode(errors="replace") if err.value else None
    if err.value:
        module.contents.free_string(err)

    return {"rc": rc, "result": result, "error": error_text}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("so", help="path to libpai_*.so")
    ap.add_argument("--describe", action="store_true",
                    help="print name/caps/perms and exit")
    ap.add_argument("--capability", help="e.g. shell.exec")
    ap.add_argument("--params", default="{}",
                    help="JSON dict of params, e.g. '{\"command\":\"pwd\"}'")
    args = ap.parse_args()

    so_path = Path(args.so).resolve()
    if not so_path.exists():
        print(f"not found: {so_path}", file=sys.stderr)
        sys.exit(2)

    lib, module = load_plugin(so_path)
    try:
        if args.describe:
            print(json.dumps(describe(lib, module), indent=2))
            return

        if not args.capability:
            print("--capability is required (or use --describe)", file=sys.stderr)
            sys.exit(2)

        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            print(f"bad --params JSON: {e}", file=sys.stderr)
            sys.exit(2)

        result = invoke(lib, module, args.capability, params)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        lib.pai_plugin_module_destroy(module)


if __name__ == "__main__":
    main()