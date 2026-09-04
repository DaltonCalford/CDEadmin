// CDEadmin TiKV bridge. The process handles one bounded JSON request and exits.
// Transaction commit/retry/finality remains owned by the pinned TiKV client.
package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/pingcap/kvproto/pkg/kvrpcpb"
	pingcaplog "github.com/pingcap/log"
	"github.com/tikv/client-go/v2/config"
	tikverr "github.com/tikv/client-go/v2/error"
	"github.com/tikv/client-go/v2/rawkv"
	"github.com/tikv/client-go/v2/txnkv"
	"go.uber.org/zap/zapcore"
)

const (
	maximumRequestBytes = 2 * 1024 * 1024
	maximumResultBytes  = 12 * 1024 * 1024
	maximumRecords      = 10000
	maximumEndpoints    = 16
)

type mutation struct {
	Operation string `json:"operation"`
	Key       string `json:"key_base64"`
	Value     string `json:"value_base64"`
}

type request struct {
	Operation               string     `json:"operation"`
	PDEndpoints             []string   `json:"pd_endpoints"`
	TLSCA                   string     `json:"tls_ca"`
	TLSCertificate          string     `json:"tls_certificate"`
	TLSKey                  string     `json:"tls_key"`
	APIVersion              int32      `json:"api_version"`
	EnableTTL               bool       `json:"enable_ttl"`
	Key                     string     `json:"key_base64"`
	Value                   string     `json:"value_base64"`
	Keys                    []string   `json:"keys_base64"`
	Values                  []string   `json:"values_base64"`
	PreviousValue           *string    `json:"previous_value_base64"`
	StartKey                string     `json:"start_key_base64"`
	EndKey                  string     `json:"end_key_base64"`
	Limit                   int        `json:"limit"`
	IncludeTTL              bool       `json:"include_ttl"`
	TTLSeconds              uint64     `json:"ttl_seconds"`
	Mutations               []mutation `json:"mutations"`
	OperationTimeoutSeconds int        `json:"operation_timeout_seconds"`
	TransactionMode         string     `json:"transaction_mode"`
}

type record struct {
	Key      string  `json:"key_base64,omitempty"`
	Value    string  `json:"value_base64,omitempty"`
	Found    *bool   `json:"found,omitempty"`
	Accepted *bool   `json:"accepted,omitempty"`
	Previous string  `json:"previous_value_base64,omitempty"`
	Swapped  *bool   `json:"swapped,omitempty"`
	TTL      *uint64 `json:"ttl_seconds_remaining,omitempty"`
}

type response struct {
	Records          []record       `json:"records"`
	Native           map[string]any `json:"native"`
	ProviderFinality bool           `json:"provider_finality_only"`
}

func decode(value string, label string) ([]byte, error) {
	decoded, err := base64.StdEncoding.DecodeString(value)
	if err != nil {
		return nil, fmt.Errorf("%s is not valid base64", label)
	}
	return decoded, nil
}

func encode(value []byte) string {
	if value == nil {
		return ""
	}
	return base64.StdEncoding.EncodeToString(value)
}

func apiVersion(value request) (kvrpcpb.APIVersion, error) {
	switch value.APIVersion {
	case 1:
		if value.EnableTTL {
			return kvrpcpb.APIVersion_V1TTL, nil
		}
		return kvrpcpb.APIVersion_V1, nil
	case 2:
		return kvrpcpb.APIVersion_V2, nil
	default:
		return 0, errors.New("api_version must be 1 or 2")
	}
}

func security(value request) config.Security {
	return config.Security{
		ClusterSSLCA:   value.TLSCA,
		ClusterSSLCert: value.TLSCertificate,
		ClusterSSLKey:  value.TLSKey,
	}
}

func boolPointer(value bool) *bool {
	return &value
}

func validateRequest(value request) error {
	if len(value.PDEndpoints) < 1 || len(value.PDEndpoints) > maximumEndpoints {
		return fmt.Errorf("pd_endpoints must contain 1 to %d entries", maximumEndpoints)
	}
	for _, endpoint := range value.PDEndpoints {
		if endpoint == "" || len(endpoint) > 512 ||
			strings.ContainsAny(endpoint, "\x00\r\n\t") {
			return errors.New("PD endpoint is invalid")
		}
	}
	if _, err := apiVersion(value); err != nil {
		return err
	}
	if (value.TLSCertificate == "") != (value.TLSKey == "") {
		return errors.New("TLS certificate and key must be provided together")
	}
	if len(value.Keys) > maximumRecords || len(value.Mutations) > maximumRecords {
		return errors.New("transaction request exceeds record limit")
	}
	if value.OperationTimeoutSeconds < 0 || value.OperationTimeoutSeconds > 3600 {
		return errors.New("operation_timeout_seconds must be between 1 and 3600")
	}
	if value.TransactionMode != "" && value.TransactionMode != "optimistic" && value.TransactionMode != "pessimistic" {
		return errors.New("transaction_mode is invalid")
	}
	if value.Operation == "transaction" && value.APIVersion == 1 && value.EnableTTL {
		return errors.New("TxnKV requires API v2 when RawKV TTL is enabled")
	}
	return nil
}

func encodedResponse(value response) ([]byte, error) {
	payload, err := json.Marshal(value)
	if err != nil {
		return nil, errors.New("response encoding failed")
	}
	if len(payload) > maximumResultBytes {
		return nil, errors.New("response exceeds size limit")
	}
	return append(payload, '\n'), nil
}

func configureLogging() error {
	sink := zapcore.AddSync(os.Stderr)
	logger, properties, err := pingcaplog.InitLoggerWithWriteSyncer(
		&pingcaplog.Config{
			Level: "warn", Format: "text", ErrorOutputPath: "stderr",
		},
		sink,
		sink,
	)
	if err != nil {
		return errors.New("helper logging configuration failed")
	}
	pingcaplog.ReplaceGlobals(logger, properties)
	return nil
}

func rawRequest(ctx context.Context, value request) (response, error) {
	version, err := apiVersion(value)
	if err != nil {
		return response{}, err
	}
	client, err := rawkv.NewClientWithOpts(
		ctx, value.PDEndpoints,
		rawkv.WithAPIVersion(version),
		rawkv.WithSecurity(security(value)),
	)
	if err != nil {
		return response{}, errors.New("TiKV RawKV connection failed")
	}
	defer client.Close()
	native := map[string]any{
		"operation":         value.Operation,
		"cluster_id":        client.ClusterID(),
		"transaction_model": "raw-single-key-or-client-batch",
	}
	result := response{Native: native, ProviderFinality: true}
	key, err := decode(value.Key, "key_base64")
	if err != nil && value.Operation != "scan" {
		return response{}, err
	}
	switch value.Operation {
	case "get":
		item, err := client.Get(ctx, key)
		if err != nil {
			return response{}, errors.New("TiKV RawKV get failed")
		}
		result.Records = []record{{
			Key: encode(key), Value: encode(item), Found: boolPointer(item != nil),
		}}
	case "put", "put_with_ttl":
		item, err := decode(value.Value, "value_base64")
		if err != nil {
			return response{}, err
		}
		if value.Operation == "put_with_ttl" && value.TTLSeconds == 0 {
			return response{}, errors.New("ttl_seconds must be greater than zero")
		}
		if value.Operation == "put_with_ttl" && !value.EnableTTL && value.APIVersion != 2 {
			return response{}, errors.New("TiKV TTL is not enabled for this route")
		}
		if err := client.PutWithTTL(ctx, key, item, value.TTLSeconds); err != nil {
			return response{}, errors.New("TiKV RawKV put failed")
		}
		result.Records = []record{{Key: encode(key), Accepted: boolPointer(true)}}
	case "get_key_ttl":
		if !value.EnableTTL && value.APIVersion != 2 {
			return response{}, errors.New("TiKV TTL is not enabled for this route")
		}
		ttl, err := client.GetKeyTTL(ctx, key)
		if err != nil {
			return response{}, errors.New("TiKV RawKV TTL read failed")
		}
		result.Records = []record{{
			Key: encode(key), Found: boolPointer(ttl != nil), TTL: ttl,
		}}
	case "delete":
		if err := client.Delete(ctx, key); err != nil {
			return response{}, errors.New("TiKV RawKV delete failed")
		}
		result.Records = []record{{Key: encode(key), Accepted: boolPointer(true)}}
	case "compare_and_swap":
		item, err := decode(value.Value, "value_base64")
		if err != nil {
			return response{}, err
		}
		var previous []byte
		if value.PreviousValue != nil {
			previous, err = decode(*value.PreviousValue, "previous_value_base64")
			if err != nil {
				return response{}, err
			}
		}
		client.SetAtomicForCAS(true)
		observed, swapped, err := client.CompareAndSwap(ctx, key, previous, item)
		if err != nil {
			return response{}, errors.New("TiKV RawKV compare-and-swap failed")
		}
		result.Records = []record{{
			Key: encode(key), Previous: encode(observed), Swapped: boolPointer(swapped),
		}}
	case "scan":
		start, err := decode(value.StartKey, "start_key_base64")
		if err != nil {
			return response{}, err
		}
		end, err := decode(value.EndKey, "end_key_base64")
		if err != nil {
			return response{}, err
		}
		limit := value.Limit
		if limit < 1 || limit > maximumRecords {
			return response{}, errors.New("scan limit is outside approved bounds")
		}
		keys, values, err := client.Scan(ctx, start, end, limit)
		if err != nil {
			return response{}, errors.New("TiKV RawKV scan failed")
		}
		result.Records = make([]record, len(keys))
		for index := range keys {
			result.Records[index] = record{Key: encode(keys[index]), Value: encode(values[index])}
			if value.IncludeTTL {
				if !value.EnableTTL && value.APIVersion != 2 {
					return response{}, errors.New("TiKV TTL is not enabled for this route")
				}
				ttl, err := client.GetKeyTTL(ctx, keys[index])
				if err != nil {
					return response{}, errors.New("TiKV RawKV TTL read failed")
				}
				result.Records[index].TTL = ttl
			}
		}
	default:
		return response{}, errors.New("RawKV operation is unsupported")
	}
	return result, nil
}

func transactionRequest(ctx context.Context, value request) (response, error) {
	version, err := apiVersion(value)
	if err != nil {
		return response{}, err
	}
	if value.TLSCA != "" || value.TLSCertificate != "" || value.TLSKey != "" {
		global := config.GetGlobalConfig()
		global.Security = security(value)
		config.StoreGlobalConfig(global)
	}
	client, err := txnkv.NewClient(
		value.PDEndpoints, txnkv.WithAPIVersion(version),
	)
	if err != nil {
		return response{}, errors.New("TiKV TxnKV connection failed")
	}
	defer client.Close()
	txn, err := client.Begin()
	if err != nil {
		return response{}, errors.New("TiKV transaction begin failed")
	}
	txn.SetPessimistic(value.TransactionMode == "pessimistic")
	rollbackRequired := true
	defer func() {
		if rollbackRequired {
			_ = txn.Rollback()
		}
	}()
	result := response{
		Records: []record{},
		Native: map[string]any{
			"operation": "transaction", "start_ts": txn.StartTS(),
			"transaction_model": "tikv-client-go-native",
			"transaction_mode":  value.TransactionMode,
			"isolation":         "snapshot-isolation",
		},
		ProviderFinality: true,
	}
	for _, encoded := range value.Keys {
		key, err := decode(encoded, "keys_base64")
		if err != nil {
			return response{}, err
		}
		entry, err := txn.Get(ctx, key)
		found := true
		if errors.Is(err, tikverr.ErrNotExist) {
			found = false
			entry.Value = nil
		} else if err != nil {
			return response{}, errors.New("TiKV transaction get failed")
		}
		result.Records = append(result.Records, record{
			Key: encode(key), Value: encode(entry.Value), Found: boolPointer(found),
		})
	}
	for _, change := range value.Mutations {
		key, err := decode(change.Key, "mutation key_base64")
		if err != nil {
			return response{}, err
		}
		switch change.Operation {
		case "set":
			item, err := decode(change.Value, "mutation value_base64")
			if err != nil {
				return response{}, err
			}
			if err := txn.Set(key, item); err != nil {
				return response{}, errors.New("TiKV transaction set failed")
			}
		case "delete":
			if err := txn.Delete(key); err != nil {
				return response{}, errors.New("TiKV transaction delete failed")
			}
		default:
			return response{}, errors.New("TiKV transaction mutation is unsupported")
		}
	}
	if len(value.Mutations) > 0 {
		result.Native["commit_requested"] = true
		// Once commit starts, the provider owns the outcome. A client-side
		// rollback attempt must not guess that an error means not committed.
		rollbackRequired = false
		if err := txn.Commit(ctx); err != nil {
			return response{}, errors.New(
				"TiKV transaction commit returned an error; outcome remains provider-owned")
		}
		result.Native["commit_returned"] = true
	} else {
		if err := txn.Rollback(); err != nil {
			return response{}, errors.New("TiKV read transaction close failed")
		}
		rollbackRequired = false
		result.Native["read_only_closed"] = true
	}
	return result, nil
}

func run() error {
	reader := bufio.NewReader(io.LimitReader(os.Stdin, maximumRequestBytes+1))
	payload, err := io.ReadAll(reader)
	if err != nil {
		return errors.New("request read failed")
	}
	if len(payload) > maximumRequestBytes {
		return errors.New("request exceeds size limit")
	}
	var value request
	if err := json.Unmarshal(payload, &value); err != nil {
		return errors.New("request is not valid JSON")
	}
	if err := validateRequest(value); err != nil {
		return err
	}
	if value.OperationTimeoutSeconds == 0 {
		value.OperationTimeoutSeconds = 30
	}
	if value.TransactionMode == "" {
		value.TransactionMode = "optimistic"
	}
	ctx, cancel := context.WithTimeout(
		context.Background(), time.Duration(value.OperationTimeoutSeconds)*time.Second)
	defer cancel()
	var result response
	if value.Operation == "transaction" {
		result, err = transactionRequest(ctx, value)
	} else {
		result, err = rawRequest(ctx, value)
	}
	if err != nil {
		return err
	}
	payload, err = encodedResponse(result)
	if err != nil {
		return err
	}
	_, err = os.Stdout.Write(payload)
	return err
}

func main() {
	if err := configureLogging(); err != nil {
		_ = json.NewEncoder(os.Stdout).Encode(map[string]string{
			"error": err.Error(),
		})
		os.Exit(1)
	}
	if err := run(); err != nil {
		_ = json.NewEncoder(os.Stdout).Encode(map[string]string{
			"error": err.Error(),
		})
		os.Exit(1)
	}
}
