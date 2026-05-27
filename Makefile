# Gabagool Bot - Makefile
# Common commands for development and operation
#
# Usage:
#   make setup     - Create venv and install dependencies
#   make run       - Run bot in dry-run mode
#   make run-live  - Run bot in live mode (CAREFUL!)
#   make test      - Run unit tests
#   make shell     - Open shell with venv activated

.PHONY: setup run run-live test shell clean help

# Default Python
PYTHON := python3
VENV := .venv
VENV_BIN := $(VENV)/bin
PIP := $(VENV_BIN)/pip
PYTEST := $(VENV_BIN)/pytest

# Colors
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m

help:
	@echo "$(GREEN)Gabagool Bot - Available Commands$(NC)"
	@echo ""
	@echo "  $(YELLOW)make setup$(NC)      - Create virtual environment and install dependencies"
	@echo "  $(YELLOW)make run$(NC)        - Run bot in dry-run mode (safe)"
	@echo "  $(YELLOW)make run-live$(NC)   - Run bot in LIVE mode (executes real trades!)"
	@echo "  $(YELLOW)make test$(NC)       - Run unit tests"
	@echo "  $(YELLOW)make test-live$(NC)  - Run live API tests (requires credentials)"
	@echo "  $(YELLOW)make shell$(NC)      - Open shell with venv activated"
	@echo "  $(YELLOW)make clean$(NC)      - Remove venv and cache files"
	@echo ""
	@echo "$(GREEN)Quick Start:$(NC)"
	@echo "  1. make setup"
	@echo "  2. cp config/.env.example config/.env  # Add your credentials"
	@echo "  3. make run"

# =============================================================================
# SETUP
# =============================================================================

$(VENV)/bin/activate:
	@echo "$(GREEN)Creating virtual environment...$(NC)"
	$(PYTHON) -m venv $(VENV)
	@echo "$(GREEN)Upgrading pip...$(NC)"
	$(PIP) install --upgrade pip

setup: $(VENV)/bin/activate
	@echo "$(GREEN)Installing dependencies...$(NC)"
	$(PIP) install -r requirements.txt
	@echo ""
	@echo "$(GREEN)Setup complete!$(NC)"
	@echo "To activate manually: source $(VENV)/bin/activate"
	@echo "Or just use: make run, make test, etc."

# =============================================================================
# RUN
# =============================================================================

run: $(VENV)/bin/activate
	@echo "$(GREEN)Running Gabagool Bot (DRY RUN)...$(NC)"
	@mkdir -p logs
	$(VENV_BIN)/python -m src.main --dry-run --log-level INFO 2>&1 | tee "logs/bot-dry-run-$$(date +'%Y-%m-%d-%H-%M').log"

run-live: $(VENV)/bin/activate
	@echo "$(RED)WARNING: Running in LIVE mode - real trades will be executed!$(NC)"
	@read -p "Are you sure? (yes/no): " confirm && [ "$$confirm" = "yes" ] || exit 1
	@mkdir -p logs
	$(VENV_BIN)/python -m src.main --config config/production.yaml --log-level INFO 2>&1 | tee "logs/bot-live-$$(date +'%Y-%m-%d-%H-%M').log"

run-debug: $(VENV)/bin/activate
	@echo "$(YELLOW)Running Gabagool Bot (DEBUG mode)...$(NC)"
	$(VENV_BIN)/python -m src.main --dry-run --log-level DEBUG

# =============================================================================
# TEST
# =============================================================================

test: $(VENV)/bin/activate
	@echo "$(GREEN)Running unit tests...$(NC)"
	$(PYTEST) tests/unit/ -v

test-live: $(VENV)/bin/activate
	@echo "$(YELLOW)Running live API tests...$(NC)"
	$(PYTEST) tests/live/ -v

test-all: $(VENV)/bin/activate
	@echo "$(GREEN)Running all tests...$(NC)"
	$(PYTEST) tests/ -v

# =============================================================================
# DEVELOPMENT
# =============================================================================

shell: $(VENV)/bin/activate
	@echo "$(GREEN)Activating virtual environment...$(NC)"
	@echo "Run 'deactivate' to exit"
	@bash --rcfile <(echo '. ~/.bashrc; source $(VENV)/bin/activate; cd $(PWD)')

lint: $(VENV)/bin/activate
	@echo "$(GREEN)Running linters...$(NC)"
	$(VENV_BIN)/python -m py_compile src/*.py strategies/*.py
	@echo "$(GREEN)Syntax OK$(NC)"

# =============================================================================
# CLEANUP
# =============================================================================

clean:
	@echo "$(YELLOW)Cleaning up...$(NC)"
	rm -rf $(VENV)
	rm -rf __pycache__ src/__pycache__ strategies/__pycache__ tests/__pycache__
	rm -rf .pytest_cache
	rm -rf *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "$(GREEN)Clean complete$(NC)"

clean-data:
	@echo "$(RED)WARNING: This will delete all data files!$(NC)"
	@read -p "Are you sure? (yes/no): " confirm && [ "$$confirm" = "yes" ] || exit 1
	rm -rf data/ logs/
	@echo "$(GREEN)Data cleaned$(NC)"
