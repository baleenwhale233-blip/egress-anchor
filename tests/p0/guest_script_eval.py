"""Headless calculation using the guest's existing JavaScriptCore C API, not a UI."""

import ctypes as c
import ctypes.util
import json
import platform
import socket
import sys


def evaluate(script):
    library = c.CDLL(ctypes.util.find_library("javascriptcoregtk-4.1"))

    def bind(name, result, arguments):
        function = getattr(library, name)
        function.restype, function.argtypes = result, arguments
        return function

    create = bind("JSGlobalContextCreate", c.c_void_p, [c.c_void_p])
    release_context = bind("JSGlobalContextRelease", None, [c.c_void_p])
    make_string = bind("JSStringCreateWithUTF8CString", c.c_void_p, [c.c_char_p])
    release_string = bind("JSStringRelease", None, [c.c_void_p])
    run = bind("JSEvaluateScript", c.c_void_p, [c.c_void_p] * 4 + [c.c_int, c.POINTER(c.c_void_p)])
    stringify = bind("JSValueToStringCopy", c.c_void_p, [c.c_void_p, c.c_void_p, c.POINTER(c.c_void_p)])
    capacity = bind("JSStringGetMaximumUTF8CStringSize", c.c_size_t, [c.c_void_p])
    copy_string = bind("JSStringGetUTF8CString", c.c_size_t, [c.c_void_p, c.c_void_p, c.c_size_t])
    context, source, output = create(None), make_string(script.encode()), None
    try:
        error = c.c_void_p()
        value = run(context, source, None, None, 1, c.byref(error))
        if error.value or not value:
            raise ValueError("fixture JavaScript evaluation failed")
        output = stringify(context, value, c.byref(error))
        if error.value or not output:
            raise ValueError("fixture JavaScript did not produce JSON")
        buffer = c.create_string_buffer(capacity(output))
        copy_string(output, buffer, len(buffer))
        return json.loads(buffer.value.decode())
    finally:
        if output:
            release_string(output)
        release_string(source)
        release_context(context)


if __name__ == "__main__":
    if platform.system() != "Linux" or socket.gethostname() != "egress-p0-client-vm":
        raise SystemExit("This fixture may run only inside the disposable P0 guest")
    print(json.dumps(evaluate(sys.stdin.read()), allow_nan=False))
