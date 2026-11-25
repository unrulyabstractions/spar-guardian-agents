package redis

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"

	goredis "github.com/redis/go-redis/v9"
	"go.flipt.io/flipt/internal/config"
)

// caBundle returns the PEM text of the extra certificate authorities the
// connection trusts: the inline bytes when set, else the file's contents.
// Neither set gives no bundle and no error.
func caBundle(cfg config.RedisCacheConfig) ([]byte, error) {
	switch {
	case cfg.CaCertBytes != "":
		return []byte(cfg.CaCertBytes), nil
	case cfg.CaCertPath != "":
		data, readErr := os.ReadFile(cfg.CaCertPath)
		if readErr != nil {
			return nil, fmt.Errorf("reading redis ca bundle %q: %w", cfg.CaCertPath, readErr)
		}
		return data, nil
	default:
		return nil, nil
	}
}

// NewClient builds the go-redis client behind the redis cache. With
// require_tls set it speaks TLS 1.2 or newer, trusts the system roots plus
// any CA bundle the config gives, and skips verification only on request.
func NewClient(cfg config.RedisCacheConfig) (*goredis.Client, error) {
	timeout := cfg.NetTimeout
	opts := &goredis.Options{
		Addr:     fmt.Sprintf("%s:%d", cfg.Host, cfg.Port),
		Username: cfg.Username,
		Password: cfg.Password,
		DB:       cfg.DB,

		PoolSize:        cfg.PoolSize,
		MinIdleConns:    cfg.MinIdleConn,
		ConnMaxIdleTime: cfg.ConnMaxIdleTime,

		DialTimeout:  timeout,
		ReadTimeout:  2 * timeout,
		WriteTimeout: 2 * timeout,
		PoolTimeout:  2 * timeout,
	}
	if !cfg.RequireTLS {
		return goredis.NewClient(opts), nil
	}

	tlsCfg := &tls.Config{
		MinVersion:         tls.VersionTLS12,
		InsecureSkipVerify: cfg.InsecureSkipTLS, // opt-in through insecure_skip_tls
	}
	bundle, err := caBundle(cfg)
	if err != nil {
		return nil, fmt.Errorf("redis tls: %w", err)
	}
	if len(bundle) != 0 {
		pool, poolErr := x509.SystemCertPool()
		if poolErr != nil {
			return nil, fmt.Errorf("redis tls: system cert pool: %w", poolErr)
		}
		pool.AppendCertsFromPEM(bundle)
		tlsCfg.RootCAs = pool
	}
	opts.TLSConfig = tlsCfg
	return goredis.NewClient(opts), nil
}
