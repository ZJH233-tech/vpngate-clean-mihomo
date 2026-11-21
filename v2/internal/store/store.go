package store

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type RawServer struct {
	HostName     string `json:"hostName"`
	IP           string `json:"ip"`
	Score        int    `json:"score"`
	Ping         int    `json:"ping"`
	Speed        int64  `json:"speed"`
	CountryShort string `json:"countryShort"`
	ConfigBase64 string `json:"configDataBase64"`
}

type IPQuality struct {
	IP        string    `json:"ip"`
	Country   string    `json:"country"`
	ISP       string    `json:"isp"`
	Org       string    `json:"org"`
	AS        string    `json:"as"`
	ASN       string    `json:"asn"`
	Proxy     bool      `json:"proxy"`
	Hosting   bool      `json:"hosting"`
	Mobile    bool      `json:"mobile"`
	UpdatedAt time.Time `json:"updated_at"`
}

type NodeFeedback struct {
	AuthFails     int       `json:"auth_fails"`
	ConnFails     int       `json:"conn_fails"`
	NonJPCount    int       `json:"non_jp_count"`
	ZombieCount   int       `json:"zombie_count"`
	CooldownUntil time.Time `json:"cooldown_until"`
	LastReason    string    `json:"last_reason,omitempty"`
	LastOK        time.Time `json:"last_ok,omitempty"`
}

type Store struct {
	dir        string
	mu         sync.RWMutex
	ipCache    map[string]IPQuality
	feedback   map[string]NodeFeedback
	rawServers []RawServer
}

func Open(dir string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	s := &Store{
		dir:      dir,
		ipCache:  make(map[string]IPQuality),
		feedback: make(map[string]NodeFeedback),
	}
	_ = readJSON(filepath.Join(dir, "ipcache.json"), &s.ipCache)
	_ = readJSON(filepath.Join(dir, "feedback.json"), &s.feedback)
	_ = readJSON(filepath.Join(dir, "raw_servers.json"), &s.rawServers)
	return s, nil
}

func readJSON(path string, dst any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, dst)
}

func atomicWriteJSON(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func (s *Store) GetIPQuality(ip string, ttl time.Duration) (IPQuality, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	q, ok := s.ipCache[ip]
	if !ok || time.Since(q.UpdatedAt) > ttl {
		return IPQuality{}, false
	}
	return q, true
}

func (s *Store) PutIPQualities(items map[string]IPQuality) {
	if len(items) == 0 {
		return
	}
	s.mu.Lock()
	for ip, q := range items {
		s.ipCache[ip] = q
	}
	// 清理超过 48h 的过期记录，避免文件无限增长
	cutoff := time.Now().Add(-48 * time.Hour)
	for ip, q := range s.ipCache {
		if q.UpdatedAt.Before(cutoff) {
			delete(s.ipCache, ip)
		}
	}
	_ = atomicWriteJSON(filepath.Join(s.dir, "ipcache.json"), s.ipCache)
	s.mu.Unlock()
}

func (s *Store) GetFeedback(ip string) NodeFeedback {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.feedback[ip]
}

func (s *Store) IsCoolingDown(ip string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	fb, ok := s.feedback[ip]
	return ok && time.Now().Before(fb.CooldownUntil)
}

func (s *Store) RecordFail(ip, kind string, cooldown time.Duration) {
	s.mu.Lock()
	fb := s.feedback[ip]
	switch kind {
	case "auth":
		fb.AuthFails++
	case "non_jp":
		fb.NonJPCount++
	case "zombie":
		fb.ZombieCount++
	default:
		fb.ConnFails++
	}
	until := time.Now().Add(cooldown)
	if until.After(fb.CooldownUntil) {
		fb.CooldownUntil = until
	}
	fb.LastReason = kind
	s.feedback[ip] = fb
	_ = atomicWriteJSON(filepath.Join(s.dir, "feedback.json"), s.feedback)
	s.mu.Unlock()
}

func (s *Store) RecordSuccess(ip string) {
	s.mu.Lock()
	fb := s.feedback[ip]
	fb.LastOK = time.Now()
	fb.CooldownUntil = time.Time{}
	fb.LastReason = ""
	s.feedback[ip] = fb
	_ = atomicWriteJSON(filepath.Join(s.dir, "feedback.json"), s.feedback)
	s.mu.Unlock()
}

func (s *Store) SaveRawServers(list []RawServer) {
	s.mu.Lock()
	s.rawServers = append([]RawServer(nil), list...)
	_ = atomicWriteJSON(filepath.Join(s.dir, "raw_servers.json"), s.rawServers)
	s.mu.Unlock()
}

func (s *Store) LoadRawServers() []RawServer {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return append([]RawServer(nil), s.rawServers...)
}
