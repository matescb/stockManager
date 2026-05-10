.PHONY: dev-up dev-down dev-logs dev-rebuild prod-up prod-logs prod-rebuild refresh-tp-spec regen-tp-models

TP_SPEC_URL := https://api.trustedparts.com/swagger/inventory-api-v2/swagger.json
TP_SPEC := docs/schemas/trustedparts-v2.json
TP_GENERATED := backend/app/domain/sourcing/_generated/trustedparts_v2.py

dev-up:
	docker compose -f docker-compose.dev.yml up --build

dev-down:
	docker compose -f docker-compose.dev.yml down

dev-logs:
	docker compose -f docker-compose.dev.yml logs -f

dev-rebuild:
	docker compose -f docker-compose.dev.yml up --build --force-recreate

prod-up:
	docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

prod-logs:
	docker compose -f docker-compose.prod.yml logs -f

prod-rebuild:
	docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build --force-recreate

refresh-tp-spec:
	mkdir -p "$(dir $(TP_SPEC))"
	curl -fsSL "$(TP_SPEC_URL)" | jq --sort-keys . > "$(TP_SPEC)"

regen-tp-models:
	mkdir -p "$(dir $(TP_GENERATED))"
	cd backend && uv run datamodel-codegen \
		--input ../$(TP_SPEC) \
		--input-file-type openapi \
		--output ../$(TP_GENERATED) \
		--output-model-type pydantic_v2.BaseModel \
		--target-python-version 3.12 \
		--target-pydantic-version 2.11 \
		--use-standard-collections \
		--use-union-operator \
		--use-annotated \
		--field-constraints \
		--use-double-quotes \
		--formatters black isort \
		--disable-timestamp \
		--custom-file-header '# AUTO-GENERATED FILE - DO NOT EDIT. Run `make regen-tp-models` from the repository root.'
