########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
#########################################################################

SHELL = /bin/sh

APP_NAME := $(shell grep ^APP_NAME web/branding.py | awk -F"=" '{print $$NF}' | tr -d '[:space:]' | tr -d "'" | awk '{print tolower($$0)}')
APP_RELEASE := $(shell grep ^APP_RELEASE web/version.py | awk -F"=" '{print $$NF}' | tr -d '[:space:]')
APP_REVISION := $(shell grep ^APP_REVISION web/version.py | awk -F"=" '{print $$NF}' | tr -d '[:space:]')

#########################################################################
# High-level targets
#########################################################################

# Include only platform-independent builds in all
all: docs pip src

# Add BUILD_OPTS variable to pass arguments
appbundle:
	./pkg/mac/build.sh $(BUILD_OPTS)

install-node:
	cd web && yarn install

install-python:
	./tools/setup-python-env.sh

install-python-testing:
	./tools/setup-python-env.sh --test

bundle:
	cd web && yarn run bundle

bundle-dev:
	cd web && yarn run bundle:dev

linter:
	cd web && yarn run linter

check: install-node bundle linter check-pep8 cdeadmin-contracts-check cdeadmin-architecture-gate cdeadmin-endpoint-persistence-test cdeadmin-provider-registry-test cdeadmin-postgresql-provider-test cdeadmin-mongodb-provider-test cdeadmin-resource-explorer-test cdeadmin-data-studio-test cdeadmin-result-renderer-test cdeadmin-semantic-model-test cdeadmin-operation-bus-test cdeadmin-security-isolation-test cdeadmin-quality-infrastructure-test cdeadmin-product-identity-test cdeadmin-product-identity-check cdeadmin-profile-migration-test cdeadmin-transport-foundations-test cdeadmin-transport-gate cdeadmin-actual-engine-pilots-test cdeadmin-actual-engine-gate cdeadmin-reference-corpus-test cdeadmin-reference-corpus-gate cdeadmin-parity-test cdeadmin-parity-gate cdeadmin-workspace-shell-test cdeadmin-workspace-shell-gate cdeadmin-scratchbird-consumer-test cdeadmin-scratchbird-consumer-gate cdeadmin-upstream-acceptance-test cdeadmin-upstream-acceptance-gate cdeadmin-quality-gate cdeadmin-bundle-regression-gate
	cd web && yarn run test:js-once && python regression/runtests.py

check-audit:
	cd web && yarn run audit

check-auditjs:
# Commented the below line to avoid vulnerability in decompress package and
# audit only dependencies folder. Refer https://www.npmjs.com/advisories/1217.
# Pull request is already been send https://github.com/kevva/decompress/pull/73,
# once fixed we will uncomment it.
#	cd web && yarn run auditjs
	cd web && yarn run auditjs --groups dependencies

check-auditjs-html:
	cd web && yarn run auditjs-html

check-auditpy:
	cd web && yarn run auditpy

check-pep8:
	pycodestyle --config=.pycodestyle docs/
	pycodestyle --config=.pycodestyle pkg/
	pycodestyle --config=.pycodestyle web/
	pycodestyle --config=.pycodestyle tools/

check-python:
	cd web && python regression/runtests.py --exclude feature_tests

check-resql:
	cd web && python regression/runtests.py --pkg resql --exclude feature_tests

check-feature: install-node bundle
	cd web && python regression/runtests.py --pkg feature_tests

check-js: install-node linter
	cd web && yarn run test:js-once

check-js-coverage:
    cd web && yarn run test:js-coverage

# Capture a read-only CDEadmin preparation baseline. The output directory is
# deliberately required and must be outside this source tree.
cdeadmin-baseline:
	@test -n "$(CDEADMIN_BASELINE_OUTPUT)" || { echo "CDEADMIN_BASELINE_OUTPUT is required"; exit 2; }
	python3 tools/cdeadmin_baseline.py --source . \
		--output "$(CDEADMIN_BASELINE_OUTPUT)" --require-clean

cdeadmin-baseline-test:
	python3 -m unittest discover -s tools/tests -p 'test_cdeadmin_*.py'

cdeadmin-architecture-gate:
	python3 tools/cdeadmin_architecture_gate.py --source . \
		--policy tools/cdeadmin_architecture_policy.json

cdeadmin-contracts-generate:
	python3 tools/cdeadmin_generate_contracts.py

cdeadmin-contracts-check:
	python3 tools/cdeadmin_generate_contracts.py --check
	python3 tools/cdeadmin_contract_kit.py \
		--manifest tools/tests/fixtures/cdeadmin_contracts/postgresql/provider_manifest.json
	python3 tools/cdeadmin_contract_kit.py \
		--manifest tools/tests/fixtures/cdeadmin_contracts/fixture/provider_manifest.json
	python3 tools/cdeadmin_contract_kit.py \
		--manifest web/pgadmin/cdeadmin/providers/postgresql/provider_manifest.json
	python3 tools/cdeadmin_provider_testkit.py \
		--manifest tools/tests/fixtures/cdeadmin_contracts/fixture/provider_manifest.json \
		--provider tools/tests/fixtures/cdeadmin_contracts/fixture/provider.py \
		--deny-network

cdeadmin-endpoint-persistence-test:
	python3 -m unittest tools.tests.test_cdeadmin_endpoint_persistence -v

cdeadmin-provider-registry-test:
	python3 -m unittest tools.tests.test_cdeadmin_provider_registry -v

cdeadmin-postgresql-provider-test:
	python3 -m unittest tools.tests.test_cdeadmin_postgresql_provider -v

cdeadmin-mongodb-provider-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_mongodb_provider \
		tools.tests.test_cdeadmin_provider_tooling -v

cdeadmin-redis-provider-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_redis_provider \
		tools.tests.test_cdeadmin_provider_tooling \
		tools.tests.test_cdeadmin_actual_engine_pilots -v

cdeadmin-resource-explorer-test:
	python3 -m unittest tools.tests.test_cdeadmin_resource_explorer -v

cdeadmin-data-studio-test:
	python3 -m unittest tools.tests.test_cdeadmin_data_studio -v

cdeadmin-result-renderer-test:
	python3 -m unittest tools.tests.test_cdeadmin_result_renderers -v

cdeadmin-semantic-model-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_semantic_models \
		tools.tests.test_cdeadmin_provider_workspace \
		tools.tests.test_cdeadmin_result_renderers -v

cdeadmin-semantic-live-duckdb:
	python3 tools/cdeadmin_relational_provider_live_verify.py \
		--engine duckdb \
		--output /tmp/cdeadmin-semantic-live-duckdb.json

cdeadmin-semantic-live-sqlite:
	python3 tools/cdeadmin_relational_provider_live_verify.py \
		--engine sqlite \
		--output /tmp/cdeadmin-semantic-live-sqlite.json

cdeadmin-operation-bus-test:
	python3 -m unittest tools.tests.test_cdeadmin_operation_bus -v

cdeadmin-security-isolation-test:
	python3 -m unittest tools.tests.test_cdeadmin_security_isolation -v

cdeadmin-quality-infrastructure-test:
	python3 -m unittest tools.tests.test_cdeadmin_quality_infrastructure -v

cdeadmin-product-identity-test:
	python3 -m unittest tools.tests.test_cdeadmin_product_identity -v

cdeadmin-product-identity-check:
	python3 tools/cdeadmin_product_identity.py --source . \
		--policy tools/cdeadmin_product_identity_policy.json

cdeadmin-product-identity-coverage:
	rm -f /tmp/cdeadmin_product_identity_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_product_identity_coverage \
		--include='*/tools/cdeadmin_product_identity.py,*/web/pgadmin/cdeadmin/product/*.py' \
		-m unittest tools.tests.test_cdeadmin_product_identity
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_product_identity_coverage \
		--include='*/tools/cdeadmin_product_identity.py,*/web/pgadmin/cdeadmin/product/*.py' \
		--fail-under=75
	rm -f /tmp/cdeadmin_product_identity_coverage

cdeadmin-branding-inventory:
	@test -n "$(CDEADMIN_BRANDING_OUTPUT)" || { echo "CDEADMIN_BRANDING_OUTPUT is required"; exit 2; }
	python3 tools/cdeadmin_product_identity.py --source . \
		--policy tools/cdeadmin_product_identity_policy.json \
		--output "$(CDEADMIN_BRANDING_OUTPUT)"

cdeadmin-profile-migration-test:
	python3 -m unittest tools.tests.test_cdeadmin_profile_migration -v

cdeadmin-profile-migration-coverage:
	rm -f /tmp/cdeadmin_profile_migration_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_profile_migration_coverage \
		--include='*/web/pgadmin/cdeadmin/migration/*.py,*/web/migrations/versions/cde_profile_migration_v1_.py' \
		-m unittest tools.tests.test_cdeadmin_profile_migration
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_profile_migration_coverage \
		--include='*/web/pgadmin/cdeadmin/migration/*.py,*/web/migrations/versions/cde_profile_migration_v1_.py' \
		--fail-under=80
	rm -f /tmp/cdeadmin_profile_migration_coverage

cdeadmin-transport-foundations-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_transport_foundations -v

cdeadmin-transport-gate:
	python3 tools/cdeadmin_transport_gate.py --source .

cdeadmin-transport-coverage:
	rm -f /tmp/cdeadmin_transport_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_transport_coverage \
		--include='*/web/pgadmin/cdeadmin/transports/*.py,*/tools/cdeadmin_transport_gate.py' \
		-m unittest tools.tests.test_cdeadmin_transport_foundations
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_transport_coverage \
		--include='*/web/pgadmin/cdeadmin/transports/*.py,*/tools/cdeadmin_transport_gate.py' \
		--fail-under=80
	rm -f /tmp/cdeadmin_transport_coverage

cdeadmin-actual-engine-pilots-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_actual_engine_pilots -v

cdeadmin-relational-driver-verification-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_relational_driver_verify -v

cdeadmin-relational-driver-verification:
	python3 tools/cdeadmin_relational_driver_verify.py --engine all

cdeadmin-actual-engine-gate:
	python3 tools/cdeadmin_actual_engine_gate.py --source .

cdeadmin-actual-engine-donor-gate:
	@test -n "$(CDEADMIN_DONOR_ROOT)" || { echo "CDEADMIN_DONOR_ROOT is required"; exit 2; }
	python3 tools/cdeadmin_actual_engine_gate.py --source . \
		--donor-root "$(CDEADMIN_DONOR_ROOT)"

cdeadmin-actual-engine-coverage:
	rm -f /tmp/cdeadmin_actual_engine_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_actual_engine_coverage \
		--include='*/web/pgadmin/cdeadmin/sdk/*.py,*/web/pgadmin/cdeadmin/providers/mysql_family/*.py,*/web/pgadmin/cdeadmin/providers/mongodb/*.py,*/web/pgadmin/cdeadmin/providers/neo4j/*.py,*/web/pgadmin/cdeadmin/providers/clickhouse/*.py,*/web/pgadmin/cdeadmin/providers/duckdb/*.py,*/tools/cdeadmin_actual_engine_gate.py' \
		-m unittest tools.tests.test_cdeadmin_actual_engine_pilots
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_actual_engine_coverage \
		--include='*/web/pgadmin/cdeadmin/sdk/*.py,*/web/pgadmin/cdeadmin/providers/mysql_family/*.py,*/web/pgadmin/cdeadmin/providers/mongodb/*.py,*/web/pgadmin/cdeadmin/providers/neo4j/*.py,*/web/pgadmin/cdeadmin/providers/clickhouse/*.py,*/web/pgadmin/cdeadmin/providers/duckdb/*.py,*/tools/cdeadmin_actual_engine_gate.py' \
		--fail-under=80
	rm -f /tmp/cdeadmin_actual_engine_coverage

cdeadmin-reference-corpus-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_reference_corpus -v

cdeadmin-reference-corpus-gate:
	python3 tools/cdeadmin_reference_corpus.py --source .

cdeadmin-reference-corpus-donor-gate:
	@test -n "$(CDEADMIN_DONOR_ROOT)" || { echo "CDEADMIN_DONOR_ROOT is required"; exit 2; }
	python3 tools/cdeadmin_reference_corpus.py --source . \
		--donor-root "$(CDEADMIN_DONOR_ROOT)"

cdeadmin-reference-corpus-coverage:
	rm -f /tmp/cdeadmin_reference_corpus_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_reference_corpus_coverage \
		--include='*/tools/cdeadmin_reference_corpus.py' \
		-m unittest tools.tests.test_cdeadmin_reference_corpus
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_reference_corpus_coverage \
		--include='*/tools/cdeadmin_reference_corpus.py' \
		--fail-under=80
	rm -f /tmp/cdeadmin_reference_corpus_coverage

cdeadmin-parity-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_parity_runner -v

cdeadmin-parity-gate:
	python3 tools/cdeadmin_parity_runner.py --source .

cdeadmin-parity-reference-gate:
	python3 tools/cdeadmin_parity_runner.py --source . \
		--require-reference-complete

cdeadmin-parity-release-gate:
	python3 tools/cdeadmin_parity_runner.py --source . --require-release

cdeadmin-parity-coverage:
	rm -f /tmp/cdeadmin_parity_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_parity_coverage \
		--include='*/tools/cdeadmin_parity_runner.py' \
		-m unittest tools.tests.test_cdeadmin_parity_runner
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_parity_coverage \
		--include='*/tools/cdeadmin_parity_runner.py' \
		--fail-under=75
	rm -f /tmp/cdeadmin_parity_coverage

cdeadmin-workspace-shell-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_workspace_shells -v

cdeadmin-workspace-shell-gate:
	python3 tools/cdeadmin_workspace_shell_gate.py --source .

cdeadmin-workspace-shell-coverage:
	rm -f /tmp/cdeadmin_workspace_shell_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_workspace_shell_coverage \
		--include='*/tools/cdeadmin_workspace_shell_gate.py' \
		-m unittest tools.tests.test_cdeadmin_workspace_shells
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_workspace_shell_coverage \
		--include='*/tools/cdeadmin_workspace_shell_gate.py' \
		--fail-under=80
	rm -f /tmp/cdeadmin_workspace_shell_coverage

cdeadmin-scratchbird-consumer-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_scratchbird_consumer -v

cdeadmin-scratchbird-consumer-gate:
	python3 tools/cdeadmin_scratchbird_consumer.py --source .

cdeadmin-scratchbird-consumer-coverage:
	rm -f /tmp/cdeadmin_scratchbird_consumer_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_scratchbird_consumer_coverage \
		--include='*/tools/cdeadmin_scratchbird_consumer.py' \
		-m unittest tools.tests.test_cdeadmin_scratchbird_consumer
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_scratchbird_consumer_coverage \
		--include='*/tools/cdeadmin_scratchbird_consumer.py' \
		--fail-under=80
	rm -f /tmp/cdeadmin_scratchbird_consumer_coverage

cdeadmin-upstream-acceptance-test:
	python3 -m unittest \
		tools.tests.test_cdeadmin_upstream_acceptance -v

cdeadmin-upstream-acceptance-gate:
	python3 tools/cdeadmin_upstream_acceptance.py --source .

cdeadmin-upstream-acceptance-coverage:
	rm -f /tmp/cdeadmin_upstream_acceptance_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_upstream_acceptance_coverage \
		--include='*/tools/cdeadmin_upstream_acceptance.py' \
		-m unittest tools.tests.test_cdeadmin_upstream_acceptance
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_upstream_acceptance_coverage \
		--include='*/tools/cdeadmin_upstream_acceptance.py' \
		--fail-under=80
	rm -f /tmp/cdeadmin_upstream_acceptance_coverage

cdeadmin-quality-gate:
	python3 tools/cdeadmin_quality_gate.py --source . \
		--policy tools/cdeadmin_quality_policy.json

cdeadmin-bundle-regression-gate:
	python3 tools/cdeadmin_quality_gate.py --source . \
		--policy tools/cdeadmin_quality_policy.json \
		--require-built-bundle

cdeadmin-quality-coverage:
	rm -f /tmp/cdeadmin_quality_coverage
	python3 -m coverage run \
		--data-file=/tmp/cdeadmin_quality_coverage \
		--include='*/tools/cdeadmin_provider_testkit.py,*/tools/cdeadmin_quality_gate.py' \
		-m unittest tools.tests.test_cdeadmin_quality_infrastructure
	python3 -m coverage report \
		--data-file=/tmp/cdeadmin_quality_coverage \
		--include='*/tools/cdeadmin_provider_testkit.py,*/tools/cdeadmin_quality_gate.py' \
		--fail-under=75
	rm -f /tmp/cdeadmin_quality_coverage

cdeadmin-jest-contract-test:
	cd web && corepack yarn run jest --runInBand \
		regression/javascript/cdeadmin/ContractCompatibility.spec.js

cdeadmin-jest-contract-coverage:
	cd web && corepack yarn run jest --runInBand --coverage \
		--collectCoverageFrom='pgadmin/cdeadmin/static/js/contracts/v1/generated.ts' \
		--coverageReporters=text-summary \
		--coverageThreshold='{"global":{"lines":90,"statements":90}}' \
		regression/javascript/cdeadmin/ContractCompatibility.spec.js

cdeadmin-dependency-licenses:
	@test -n "$(CDEADMIN_LICENSE_OUTPUT)" || { echo "CDEADMIN_LICENSE_OUTPUT is required"; exit 2; }
	@test -n "$(CDEADMIN_BASELINE_ID)" || { echo "CDEADMIN_BASELINE_ID is required"; exit 2; }
	python3 tools/cdeadmin_dependency_licenses.py \
		--output "$(CDEADMIN_LICENSE_OUTPUT)" \
		--baseline-id "$(CDEADMIN_BASELINE_ID)" \
		$(foreach root,$(CDEADMIN_NODE_ROOTS),--node-root "$(root)")

# Include all clean sub-targets in clean
clean: clean-appbundle clean-debian clean-dist clean-docs clean-node clean-pip clean-redhat clean-src
	rm -rf web/pgadmin/static/js/generated/*
	rm -rf web/pgadmin/static/js/generated/.cache
	rm -rf web/pgadmin/static/css/generated/*
	rm -rf web/pgadmin/static/css/generated/.cache

clean-appbundle:
	rm -rf mac-build/

clean-debian:
	rm -rf debian-build/

clean-dist:
	rm -rf dist/

clean-docs:
	LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 $(MAKE) -C docs/en_US -f Makefile.sphinx clean

clean-node:
	rm -rf web/node-modules/

clean-pip:
	rm -rf pip-build/

clean-redhat:
	rm -rf redhat-build/

clean-src:
	rm -rf src-build/

debian:
	./pkg/debian/build.sh

docker:
	echo $(APP_NAME)
	git checkout HEAD
	docker build --pull -t ${APP_NAME} -t $(APP_NAME):latest -t $(APP_NAME):$(APP_RELEASE) -t $(APP_NAME):$(APP_RELEASE).$(APP_REVISION) .

docs:
	LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 $(MAKE) -C docs/en_US -f Makefile.sphinx html

docs-pdf:
	LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 $(MAKE) -C docs/en_US -f Makefile.sphinx latexpdf

docs-epub:
	LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 $(MAKE) -C docs/en_US -f Makefile.sphinx epub

messages: msg-extract msg-update msg-compile

msg-compile:
	cd web && pybabel compile --statistics -d pgadmin/translations

msg-extract:
	cd web && pybabel extract -F babel.cfg -o pgadmin/messages.pot pgadmin

msg-update:
	cd web && pybabel update --no-fuzzy-matching -i pgadmin/messages.pot -d pgadmin/translations

.PHONY: docs cdeadmin-baseline cdeadmin-baseline-test
.PHONY: cdeadmin-architecture-gate cdeadmin-dependency-licenses
.PHONY: cdeadmin-contracts-generate cdeadmin-contracts-check
.PHONY: cdeadmin-endpoint-persistence-test
.PHONY: cdeadmin-provider-registry-test
.PHONY: cdeadmin-postgresql-provider-test
.PHONY: cdeadmin-mongodb-provider-test
.PHONY: cdeadmin-redis-provider-test
.PHONY: cdeadmin-resource-explorer-test
.PHONY: cdeadmin-data-studio-test
.PHONY: cdeadmin-result-renderer-test
.PHONY: cdeadmin-semantic-model-test
.PHONY: cdeadmin-semantic-live-duckdb cdeadmin-semantic-live-sqlite
.PHONY: cdeadmin-operation-bus-test
.PHONY: cdeadmin-security-isolation-test
.PHONY: cdeadmin-transport-foundations-test cdeadmin-transport-gate
.PHONY: cdeadmin-transport-coverage
.PHONY: cdeadmin-actual-engine-pilots-test cdeadmin-actual-engine-gate
.PHONY: cdeadmin-actual-engine-donor-gate cdeadmin-actual-engine-coverage
.PHONY: cdeadmin-reference-corpus-test cdeadmin-reference-corpus-gate
.PHONY: cdeadmin-reference-corpus-donor-gate
.PHONY: cdeadmin-reference-corpus-coverage
.PHONY: cdeadmin-parity-test cdeadmin-parity-gate
.PHONY: cdeadmin-parity-reference-gate cdeadmin-parity-release-gate
.PHONY: cdeadmin-parity-coverage
.PHONY: cdeadmin-workspace-shell-test cdeadmin-workspace-shell-gate
.PHONY: cdeadmin-workspace-shell-coverage
.PHONY: cdeadmin-scratchbird-consumer-test
.PHONY: cdeadmin-scratchbird-consumer-gate
.PHONY: cdeadmin-scratchbird-consumer-coverage
.PHONY: cdeadmin-upstream-acceptance-test
.PHONY: cdeadmin-upstream-acceptance-gate
.PHONY: cdeadmin-upstream-acceptance-coverage
.PHONY: cdeadmin-quality-infrastructure-test cdeadmin-quality-gate
.PHONY: cdeadmin-bundle-regression-gate cdeadmin-quality-coverage
.PHONY: cdeadmin-jest-contract-test cdeadmin-jest-contract-coverage

pip: docs
	./pkg/pip/build.sh

redhat:
	./pkg/redhat/build.sh

src:
	./pkg/src/build.sh
