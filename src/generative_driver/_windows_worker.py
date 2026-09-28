"""Own a Windows agent process tree in a job before the runtime can spawn.

The wrapper joins the job before launching the runtime. The OS closes its only
job handle when it exits or is killed, stopping remaining stage descendants.
This file is invoked only on Windows and has no imports into other platforms.
"""
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys


def main():
    if os.name != 'nt':
        raise RuntimeError('Windows worker wrapper was selected on a different platform')
    size=ctypes.c_size_t
    class Limits(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),
            ('flags',wintypes.DWORD),('minimum_working_set',size),('maximum_working_set',size),
            ('active_processes',wintypes.DWORD),('affinity',size),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ('read_operations','write_operations','other_operations',
                                                      'read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Limits),('io',Counters),('process_memory',size),('job_memory',size),
                  ('peak_process_memory',size),('peak_job_memory',size)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.SetInformationJobObject.restype=wintypes.BOOL
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype=wintypes.BOOL
    job=kernel.CreateJobObjectW(None,None)
    limits=Extended()
    limits.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    # The job handle is not inheritable. Keep it open until OS process teardown;
    # closing it explicitly here would also terminate this wrapper prematurely.
    code=subprocess.call(sys.argv[1:])
    os._exit(code if 0<=code<=255 else 1)


if __name__=='__main__':
    main()
