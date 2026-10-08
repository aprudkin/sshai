//go:build !windows

package transport

import (
	"os"
	"syscall"
)

func controlDirOwned(info os.FileInfo) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	// Widen both values so the platform int UID is never truncated.
	return ok && int64(stat.Uid) == int64(os.Getuid())
}
