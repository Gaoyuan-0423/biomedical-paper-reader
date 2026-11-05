//go:build !windows

package main

import "os/exec"

func hideServiceConsole(command *exec.Cmd) {}
