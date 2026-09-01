SHELL:=/bin/bash
terraform/%:
ifndef ENV
	@echo "ENV not defined, try :"
	@echo "make ENV=dev terraform/validate"
	@echo
	@exit 1
endif
	@make ENV=$(ENV) -C $(CURDIR)/infra/terraform $@
