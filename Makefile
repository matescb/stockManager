.PHONY: dev-up dev-down dev-logs dev-rebuild prod-up prod-logs prod-rebuild

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
