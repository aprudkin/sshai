package transport

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// Use the smaller macOS sockaddr_un bound on all Unix clients. OpenSSH
// expands %C to 40 bytes and binds a temporary listener with a .<16-byte>
// suffix before linking it to the final path; both must fit, including NUL.
const controlSocketPathLimit = 104

func controlSocketDir(dir string) (string, error) {
	dir, err := filepath.Abs(dir)
	if err != nil {
		return "", err
	}
	if len(dir)+1+40+17 < controlSocketPathLimit && !strings.ContainsAny(dir, "%$'\"\\\n\r\t ") {
		return dir, nil
	}
	// Do not use TMPDIR: on macOS it is commonly too long as well. The
	// stable namespace preserves sharing between invocations of the same
	// root/user, without placing artifacts or other runtime state in /tmp.
	hash := sha256.Sum256([]byte(fmt.Sprintf("%d\x00%s", os.Getuid(), dir)))
	return filepath.Join("/tmp", fmt.Sprintf("sshai-cm-%x", hash[:12])), nil
}

func (tr *OpenSSH) prepareControlDir() error {
	if !openSSHControlMasterSupported {
		return nil
	}
	tr.controlOnce.Do(func() {
		if tr.controlErr != nil {
			return
		}
		if err := os.MkdirAll(tr.controlDir, 0700); err != nil {
			tr.controlErr = err
			return
		}
		info, err := os.Lstat(tr.controlDir)
		if err != nil {
			tr.controlErr = err
			return
		}
		if !info.IsDir() || info.Mode().Perm() != 0700 || !controlDirOwned(info) {
			tr.controlErr = fmt.Errorf("control directory is not private")
		}
	})
	return tr.controlErr
}
