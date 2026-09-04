.PHONY: up down logs seed reset test lint fmt web api-shell db-shell verify verify-redis

up:            ## поднять всё: интерфейс, бэкенд, шлюз, базу, хранилище
	docker compose up -d --build
	@echo "Интерфейс → http://localhost:5173"
	@echo "API       → http://localhost:8000/docs"
	@echo "Файлы     → http://localhost:9001"
	@echo "Демо-данные: make seed"

down:
	docker compose down

reset:         ## снести данные и поднять заново
	docker compose down -v
	docker compose up -d --build
	sleep 8
	$(MAKE) seed

logs:
	docker compose logs -f api gateway scheduler

seed:          ## залить демо-данные
	docker compose exec -T api python -m app.seed.run

test:          ## тесты фронтенда и сквозная проверка API
	cd frontend && npm run test -- --run
	docker compose exec -T api python -m tests.smoke_api

smoke:         ## сквозная проверка API на демо-данных
	docker compose exec -T api python -m tests.smoke_api

verify:        ## полная проверка: соответствие доске, сходимость аналитики, работоспособность действий
	docker compose exec -T api python -m tests.smoke_api
	docker compose exec -T api python -m tests.verify_miro
	docker compose exec -T api python -m tests.verify_analytics
	docker compose exec -T api python -m tests.verify_tz
	docker compose exec -T api python -m tests.verify_gateway
	docker compose exec -T api python -m tests.verify_payments
	docker compose exec -T api python -m tests.verify_robokassa
	docker compose exec -T api python -m tests.verify_exports
	docker compose exec -T api python -m tests.verify_actions
	@echo "Действия изменили демо-данные — выполните make seed"
	$(MAKE) seed

build:         ## продакшен-сборка фронтенда
	cd frontend && npm run build

verify-redis:  ## проверка обещания «потеря Redis ничего не ломает»: гасим Redis и работаем
	@echo "Останавливаю Redis..."
	@docker compose stop cache >/dev/null
	@sleep 2
	-@docker compose exec -T api python -m tests.verify_without_redis
	@echo "Поднимаю Redis обратно..."
	@docker compose start cache >/dev/null
	@sleep 3
	@docker compose exec -T api python -c "import asyncio;from app.core import redis_bus;asyncio.run(redis_bus.publish('probe',{}));print('Redis снова отвечает')"

lint:
	docker compose exec -T api ruff check app
	cd frontend && npm run lint && npx tsc --noEmit

fmt:
	docker compose exec -T api ruff format app
	cd frontend && npm run format

web:           ## запустить фронтенд локально
	cd frontend && npm run dev

api-shell:
	docker compose exec api bash

db-shell:
	docker compose exec db psql -U astra -d astra
