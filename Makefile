# Variables
DOCKER_COMPOSE = docker compose
EXEC_BACKEND = $(DOCKER_COMPOSE) exec web
DB_CONTAINER = db


.PHONY: help build up down restart logs shell dbshell migrate makemigrations test

help: ## Show this help message
	@echo "Usage: make [command]"

build: ## Build the docker containers
	$(DOCKER_COMPOSE) build

up: ## Start the application in detached mode
	$(DOCKER_COMPOSE) up -d

down: ## Stop containers safely WITHOUT wiping your database
	$(DOCKER_COMPOSE) down

clean: ## Stop containers and WIPE all data volumes (Use with caution!)
	$(DOCKER_COMPOSE) down -v

logs: ## Tail the logs of all containers
	$(DOCKER_COMPOSE) logs -f

dev: ## Start Django backend specifically (with logs attached)
	$(DOCKER_COMPOSE) up --build web

shell: ## Open a Django Python shell inside the container
	$(EXEC_BACKEND) python manage.py shell

backendshell: 
	$(EXEC_BACKEND) /bin/bash

dbshell: ## Open a PostgreSQL shell inside the DB container
	$(DOCKER_COMPOSE) exec $(DB_CONTAINER) psql -U workstackuser -d workstack

migrate: ## Run Django database migrations
	$(EXEC_BACKEND) python manage.py migrate

makemigrations: ## Create new Django migrations
	$(EXEC_BACKEND) python manage.py makemigrations
