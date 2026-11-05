package main

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"strconv"
	"sync"
	"time"
)

type serviceReady struct {
	Protocol int    `json:"protocol"`
	URL      string `json:"url"`
	PID      int    `json:"pid"`
	Frozen   bool   `json:"frozen"`
}

type serviceProcess struct {
	cmd           *exec.Cmd
	stdin         io.WriteCloser
	done          chan struct{}
	closeOnce     sync.Once
	ready         serviceReady
	token         string
	dataDirectory string
}

func validServiceURL(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
		return false
	}
	port, err := strconv.Atoi(u.Port())
	return err == nil && port > 0 && port <= 65535
}

func startService(executable, diagnosticData string) (*serviceProcess, error) {
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return nil, err
	}
	dataDirectory := serviceDataDirectory(diagnosticData)
	p := &serviceProcess{cmd: exec.Command(executable), token: hex.EncodeToString(secret), done: make(chan struct{}), dataDirectory: dataDirectory}
	hideServiceConsole(p.cmd)
	// Explorer-launched GUI executables may have no valid console handles.
	// A nil writer makes os/exec attach the child to the null device instead.
	if os.Stderr != nil {
		if _, err := os.Stderr.Stat(); err == nil {
			p.cmd.Stderr = os.Stderr
		}
	}
	var err error
	if p.stdin, err = p.cmd.StdinPipe(); err != nil {
		return nil, err
	}
	stdout, err := p.cmd.StdoutPipe()
	if err != nil {
		p.stdin.Close()
		return nil, err
	}
	if err = p.cmd.Start(); err != nil {
		p.stdin.Close()
		return nil, fmt.Errorf("启动阅读服务失败，请保留完整应用文件夹: %w", err)
	}
	go func() { _ = p.cmd.Wait(); close(p.done) }()
	handshake := map[string]string{"token": p.token}
	if diagnosticData != "" {
		handshake["diagnostic_data"] = diagnosticData
	}
	if err = json.NewEncoder(p.stdin).Encode(handshake); err != nil {
		p.stop()
		return nil, err
	}
	type readyResult struct {
		value serviceReady
		err   error
	}
	ready := make(chan readyResult, 1)
	go func() {
		line, e := bufio.NewReader(io.LimitReader(stdout, 8192)).ReadBytes('\n')
		var value serviceReady
		if e == nil {
			e = json.Unmarshal(line, &value)
		}
		ready <- readyResult{value, e}
		_, _ = io.Copy(io.Discard, stdout)
	}()
	select {
	case result := <-ready:
		if result.err != nil || result.value.Protocol != 1 || result.value.PID != p.cmd.Process.Pid || !validServiceURL(result.value.URL) {
			p.stop()
			return nil, errors.New("阅读服务未返回有效的启动信息")
		}
		p.ready = result.value
		return p, nil
	case <-p.done:
		p.stop()
		return nil, errors.New("阅读服务启动时退出")
	case <-time.After(40 * time.Second):
		p.stop()
		return nil, errors.New("阅读服务启动超时")
	}
}

func (p *serviceProcess) bootstrapURL() string {
	return p.ready.URL + "/?desktop_token=" + p.token
}

func (p *serviceProcess) stop() {
	p.closeOnce.Do(func() {
		_ = p.stdin.Close()
		select {
		case <-p.done:
		case <-time.After(6 * time.Second):
			_ = p.cmd.Process.Kill()
			<-p.done
		}
	})
}
