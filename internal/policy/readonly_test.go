// internal/policy/readonly_test.go
package policy

import "testing"

func TestReadonlyOffAllowsEverything(t *testing.T) {
	if err := CheckReadonly("rm -rf /", false); err != nil {
		t.Fatal(err)
	}
}

func TestReadonlyAllowsReads(t *testing.T) {
	for _, cmd := range []string{
		"df -h", "journalctl -u nginx --since -1h", "systemctl status nginx",
		"Get-Service | Where-Object Status -eq Running", "cat /etc/hosts | grep local",
	} {
		if err := CheckReadonly(cmd, true); err != nil {
			t.Errorf("%q wrongly denied: %v", cmd, err)
		}
	}
}

func TestReadonlyDeniesWritesAndSmuggling(t *testing.T) {
	for _, cmd := range []string{
		"rm -f /tmp/x", "systemctl restart nginx", "Set-Service -Name x -Status Stopped",
		"df -h && rm -rf /", "cat /etc/passwd; useradd evil", "", "  ",
		"df -h || rm -rf /", "df -h | rm -rf /", "df -h\nrm -rf /",
		"df -h $(rm -rf /)", "df -h `rm -rf /`", "df -h & rm -rf /",
		"cat /etc/hosts > /tmp/copy", "cat < /etc/hosts",
		`Get-Process (Remove-Item -Force C:\temp\x)`,
		`Get-Process @(Remove-Item -Force C:\temp\x)`,
		`Get-Process | Where-Object { Remove-Item -Force C:\temp\x }`,
	} {
		if err := CheckReadonly(cmd, true); err == nil {
			t.Errorf("%q wrongly allowed", cmd)
		}
	}
}
