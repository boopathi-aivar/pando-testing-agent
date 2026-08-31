.PHONY: dev dev-frontend dev-backend install install-frontend install-backend

dev-frontend:
	cd frontend && npm run dev

dev-backend:
	cd backend && uvicorn main:app --reload --port 3001

dev:
	@echo "Starting Pando Testing Agent (backend + frontend)..."
	@make dev-backend & make dev-frontend

install-frontend:
	cd frontend && npm install

install-backend:
	cd backend && pip install -r requirements.txt

install: install-frontend install-backend
