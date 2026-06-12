#include <ntddk.h>

extern "C" NTSTATUS WriteLogFile(const char* message)
{
    UNICODE_STRING uniName;
    OBJECT_ATTRIBUTES objAttr;
    RtlInitUnicodeString(&uniName, L"\\SystemRoot\\Temp\\hyperram.log");
    InitializeObjectAttributes(&objAttr, &uniName, OBJ_CASE_INSENSITIVE | OBJ_KERNEL_HANDLE, NULL, NULL);

    HANDLE handle;
    IO_STATUS_BLOCK ioStatusBlock;

    NTSTATUS status = ZwCreateFile(&handle,
        FILE_APPEND_DATA | SYNCHRONIZE,
        &objAttr,
        &ioStatusBlock,
        NULL,
        FILE_ATTRIBUTE_NORMAL,
        FILE_SHARE_READ,
        FILE_OPEN_IF,
        FILE_SYNCHRONOUS_IO_NONALERT,
        NULL,
        0);

    if (NT_SUCCESS(status)) {
        ULONG len = 0;
        while (message[len] != '\0') len++;
        ZwWriteFile(handle, NULL, NULL, NULL, &ioStatusBlock, (PVOID)message, len, NULL, NULL);
        ZwClose(handle);
    }
    return status;
}

extern "C" VOID DriverUnload(PDRIVER_OBJECT DriverObject)
{
    UNREFERENCED_PARAMETER(DriverObject);
    WriteLogFile("[HyperRAM-WDM] Unload called.\r\n");
}

extern "C" NTSTATUS NTAPI DriverEntry(
    _In_ PDRIVER_OBJECT  DriverObject,
    _In_ PUNICODE_STRING RegistryPath
)
{
    UNREFERENCED_PARAMETER(RegistryPath);
    WriteLogFile("----------------------------------------\r\n");
    WriteLogFile("[HyperRAM-WDM] DriverEntry entered.\r\n");

    DriverObject->DriverUnload = DriverUnload;
    
    return STATUS_SUCCESS;
}
