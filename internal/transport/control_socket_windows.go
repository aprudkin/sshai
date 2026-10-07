package transport

import "os"

// Windows OpenSSH does not use the Unix control-socket options.
func controlDirOwned(os.FileInfo) bool { return false }
