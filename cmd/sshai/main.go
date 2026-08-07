package main

import (
	"fmt"
	"os"

	"github.com/aprudkin/sshai/internal/cli"
)

const (
	exitUsage     = 96
	exitPolicy    = 97
	exitTransport = 98
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: sshai <run|q|diff|log|hosts|gc|help> ...")
		os.Exit(exitUsage)
	}
	cmds := map[string]func([]string) int{
		"run": cmdRun, "q": cmdQ, "diff": cmdDiff, "log": cmdLog,
		"hosts": cmdHosts, "gc": cmdGc, "help": cmdHelp,
	}
	fn, ok := cmds[os.Args[1]]
	if !ok {
		fmt.Fprintf(os.Stderr, "unknown command %q\n", os.Args[1])
		os.Exit(exitUsage)
	}
	os.Exit(fn(os.Args[2:]))
}

func notImplemented(name string) int {
	fmt.Fprintf(os.Stderr, "%s: not implemented yet\n", name)
	return exitUsage
}

func cmdRun(a []string) int   { return cli.Run(a, os.Stdout, os.Stderr) }
func cmdQ(a []string) int     { return cli.Q(a, os.Stdout, os.Stderr) }
func cmdDiff(a []string) int  { return cli.Diff(a, os.Stdout, os.Stderr) }
func cmdLog(a []string) int   { return cli.Log(a, os.Stdout, os.Stderr) }
func cmdHosts(a []string) int { return cli.Hosts(a, os.Stdout, os.Stderr) }
func cmdGc(a []string) int    { return cli.Gc(a, os.Stdout, os.Stderr) }
func cmdHelp(a []string) int  { return notImplemented("help") }
