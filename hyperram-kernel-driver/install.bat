@echo off
cd /d "C:\Users\manth\Downloads\ssd into ram\hyperram-kernel-driver"
echo === Signing === > install_log.txt
powershell -NoProfile -ExecutionPolicy Bypass -File sign_driver.ps1 >> install_log.txt 2>&1
echo === Stopping === >> install_log.txt
sc.exe stop HyperRAM >> install_log.txt 2>&1
timeout /t 1 /nobreak > nul
sc.exe delete HyperRAM >> install_log.txt 2>&1
timeout /t 1 /nobreak > nul
echo === Copying === >> install_log.txt
copy /y HyperRAM.sys C:\Windows\System32\drivers\HyperRAM.sys >> install_log.txt 2>&1
echo === Creating === >> install_log.txt
sc.exe create HyperRAM type= kernel start= demand error= normal binPath= "\SystemRoot\System32\drivers\HyperRAM.sys" DisplayName= "HyperRAM" >> install_log.txt 2>&1
echo === Registry === >> install_log.txt
reg add HKLM\SYSTEM\CurrentControlSet\Services\HyperRAM\Parameters /v PoolFilePath /t REG_SZ /d "\??\C:\hyperram.pool" /f >> install_log.txt 2>&1
reg add HKLM\SYSTEM\CurrentControlSet\Services\HyperRAM\Parameters /v PoolSizeMB /t REG_DWORD /d 256 /f >> install_log.txt 2>&1
reg add HKLM\SYSTEM\CurrentControlSet\Services\HyperRAM\Parameters /v RamCacheMB /t REG_DWORD /d 64 /f >> install_log.txt 2>&1
echo === Starting === >> install_log.txt
sc.exe start HyperRAM >> install_log.txt 2>&1
echo === Done === >> install_log.txt
