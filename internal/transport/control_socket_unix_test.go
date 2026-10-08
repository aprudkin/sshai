//go:build !windows

package transport

import (
	"os"
	"syscall"
	"testing"
)

type controlOwnerInfo struct {
	os.FileInfo
	stat any
}

func (info controlOwnerInfo) Sys() any { return info.stat }

func TestControlDirOwned(t *testing.T) {
	info, err := os.Stat(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	stat := *info.Sys().(*syscall.Stat_t)
	other := stat
	other.Uid ^= 1
	maxUID := stat
	maxUID.Uid = 1<<32 - 1
	for _, tc := range []struct {
		name string
		stat any
		want bool
	}{
		{"current owner", &stat, true},
		{"different owner", &other, false},
		{"maximum UID", &maxUID, false},
		{"missing stat", nil, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := controlDirOwned(controlOwnerInfo{FileInfo: info, stat: tc.stat}); got != tc.want {
				t.Fatalf("owned=%v, want %v", got, tc.want)
			}
		})
	}
}
