//go:build !windows

package transport

import (
	"os"
	"syscall"
)

func controlDirOwned(info os.FileInfo) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && stat.Uid == uint32(os.Getuid())
}
