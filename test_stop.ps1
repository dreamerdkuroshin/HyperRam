$logFile = "c:\Users\manth\Downloads\ssd into ram\stop_log.txt"
Add-Content -Path $logFile -Value "--- Stop Attempt ---"
& sc.exe stop HyperRAM 2>&1 | Add-Content -Path $logFile
& sc.exe query HyperRAM 2>&1 | Add-Content -Path $logFile
Add-Content -Path $logFile -Value "--- Done ---"
