package cli

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

type resultOutTemp interface {
	Name() string
	Chmod(os.FileMode) error
	Write([]byte) (int, error)
	Sync() error
	Close() error
}

// writeResultOut publishes one private JSON document atomically when path is
// non-empty. Existing regular files are replaced; symlinks and other
// non-regular paths are refused. The temporary file is created beside the
// destination so rename remains atomic.
func writeResultOut(path string, document []byte, stderr io.Writer) int {
	return writeResultOutWith(func(dir, pattern string) (resultOutTemp, error) {
		return os.CreateTemp(dir, pattern)
	}, path, document, stderr)
}

func writeResultOutWith(createTemp func(string, string) (resultOutTemp, error), path string, document []byte, stderr io.Writer) int {
	if path == "" {
		return 0
	}
	if fi, err := os.Lstat(path); err == nil {
		if !fi.Mode().IsRegular() {
			fmt.Fprintf(stderr, "run: --result-out: %s is not a regular file\n", path)
			return exitUsage
		}
	} else if !os.IsNotExist(err) {
		fmt.Fprintf(stderr, "run: --result-out: %v\n", err)
		return exitUsage
	}

	dir := filepath.Dir(path)
	tmp, err := createTemp(dir, "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		fmt.Fprintf(stderr, "run: --result-out: %v\n", err)
		return exitUsage
	}
	tmpName := tmp.Name()
	removeTemp := true
	defer func() {
		if removeTemp {
			_ = os.Remove(tmpName)
		}
	}()
	if err := tmp.Chmod(0o600); err != nil {
		fmt.Fprintf(stderr, "run: --result-out: %v\n", err)
		_ = tmp.Close()
		return exitUsage
	}
	output := append(bytes.Clone(document), '\n')
	if _, err := tmp.Write(output); err != nil {
		fmt.Fprintf(stderr, "run: --result-out: %v\n", err)
		_ = tmp.Close()
		return exitUsage
	}
	if err := tmp.Sync(); err != nil {
		fmt.Fprintf(stderr, "run: --result-out: %v\n", err)
		_ = tmp.Close()
		return exitUsage
	}
	if err := tmp.Close(); err != nil {
		fmt.Fprintf(stderr, "run: --result-out: %v\n", err)
		return exitUsage
	}
	if err := os.Rename(tmpName, path); err != nil {
		fmt.Fprintf(stderr, "run: --result-out: %v\n", err)
		return exitUsage
	}
	removeTemp = false
	return 0
}
