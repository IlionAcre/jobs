"""
Upwork scraper pipeline: scheduler -> fetcher -> dispatcher, connected by a queue.

Design and decisions: docs/adr/0002-scraper-pipeline.md. Site behaviour this relies on:
research/upwork_recon/. Tunables: app/config/scraper.yaml.
"""
