Set-Location "c:\Users\manth\Downloads\ssd into ram\hyperram-kernel-driver"
& .\sign_driver.ps1
Copy-Item .\HyperRAM.sys C:\Windows\System32\drivers\HyperRAM.sys -Force
sc.exe stop HyperRAM
Start-Sleep -Seconds 1
sc.exe delete HyperRAM
Start-Sleep -Seconds 1
sc.exe create HyperRAM type= kernel start= demand error= normal binPath= \SystemRoot\System32\drivers\HyperRAM.sys DisplayName= HyperRAM
Start-Sleep -Seconds 1
sc.exe start HyperRAM
"Driver installed and started successfully!" | Out-File "c:\Users\manth\Downloads\ssd into ram\hyperram-kernel-driver\install_success.txt"
