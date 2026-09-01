SHELL:=/bin/bash
TERRAFORM_BIN:=terraform

.PHONY: terraform/init terraform/configure terraform/validate

terraform/init:
ifndef ENV
	@echo "ENV not defined, try :"
	@echo "make ENV=dev terraform/init"
	@echo
	@exit 1
endif
	$(TERRAFORM_BIN) init -input=false -backend=false

terraform/configure:
ifndef ENV
	@echo "ENV not defined, try :"
	@echo "make ENV=dev terraform/validate"
	@echo
	@exit 1
endif
ifeq (,$(wildcard $(CURDIR)/config_env/$(ENV)/config.yaml))
	@echo "Invalid environment $(ENV) — no config_env/$(ENV)/config.yaml"
	@echo
	@exit 1
endif

terraform/validate: terraform/configure
	$(TERRAFORM_BIN) validate
