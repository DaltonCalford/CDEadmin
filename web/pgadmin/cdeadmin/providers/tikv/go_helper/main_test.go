package main

import (
	"encoding/base64"
	"strings"
	"testing"
)

func TestAPIVersionRejectsUnknownValue(t *testing.T) {
	if _, err := apiVersion(3); err == nil {
		t.Fatal("unknown API version was accepted")
	}
}

func TestDecodeRejectsInvalidBase64(t *testing.T) {
	if _, err := decode("%%%", "key_base64"); err == nil {
		t.Fatal("invalid base64 was accepted")
	}
	value := base64.StdEncoding.EncodeToString([]byte("key"))
	decoded, err := decode(value, "key_base64")
	if err != nil || string(decoded) != "key" {
		t.Fatalf("valid base64 did not round trip: %q, %v", decoded, err)
	}
}

func TestValidateRequestBoundsAndTLS(t *testing.T) {
	valid := request{PDEndpoints: []string{"127.0.0.1:2379"}, APIVersion: 2}
	if err := validateRequest(valid); err != nil {
		t.Fatalf("valid request rejected: %v", err)
	}
	valid.PDEndpoints = []string{"bad\nendpoint"}
	if err := validateRequest(valid); err == nil {
		t.Fatal("endpoint containing a control character was accepted")
	}
	valid.PDEndpoints = []string{"127.0.0.1:2379"}
	valid.TLSCertificate = "client.pem"
	if err := validateRequest(valid); err == nil {
		t.Fatal("TLS certificate without key was accepted")
	}
}

func TestEncodedResponseIsBounded(t *testing.T) {
	value := response{Records: []record{{
		Value: strings.Repeat("x", maximumResultBytes),
	}}}
	if _, err := encodedResponse(value); err == nil {
		t.Fatal("oversized response was accepted")
	}
}
