import json, sys, importlib.util

def main(plugin_dir: str):
    spec = importlib.util.spec_from_file_location("plugin", f"{plugin_dir}/plugin.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plugin = mod.Plugin({})

    for line in sys.stdin:
        msg = json.loads(line)
        try:
            result = plugin.invoke(msg["capability"], msg["parameters"])
            # handle async
            if hasattr(result, "__await__"):
                import asyncio
                result = asyncio.get_event_loop().run_until_complete(result)
            out = {"ok": True, "data": result}
        except Exception as exc:
            out = {"ok": False, "error": repr(exc)}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main(sys.argv[1])