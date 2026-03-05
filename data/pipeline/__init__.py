"""Data pipeline package — Kafka producers/consumers and entity resolution.

Components:
    kafka_producer.py    — publish validated events to Kafka topics
    kafka_consumer.py    — consume and process events from Kafka topics
    entity_resolution.py — map raw company name strings to canonical supplier IDs
"""
