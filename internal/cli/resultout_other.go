//go:build js || windows || plan9 || wasip1

package cli

import "os"

func openResultOut(path string, flags int, perm os.FileMode) (*os.File, error) {
	return os.OpenFile(path, flags, perm)
}
