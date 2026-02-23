import pytest
from playwright.sync_api import Page, expect

def test_sidebar_groups_and_tabs(page: Page, server: str):
    page.goto(server)
    
    # Verify Hub Labels (Phase 3 work)
    expect(page.get_by_text("Status", exact=True)).to_be_visible()
    expect(page.get_by_text("Configure", exact=True)).to_be_visible()
    expect(page.get_by_text("Maintenance", exact=True)).to_be_visible()
    expect(page.get_by_text("Analysis", exact=True)).to_be_visible()

    # Verify Tab Switching
    page.get_by_text("Parameters").click()
    expect(page.locator("#parameters-tab")).to_be_visible()
    
    page.get_by_text("Drone View").click()
    expect(page.locator("#drone-view-tab")).to_be_visible()

def test_connection_modal(page: Page, server: str):
    page.goto(server)
    
    # Click Connect
    page.locator("#connectButton").click()
    expect(page.locator("#connectionModal")).to_be_visible()
    
    # Switch to IP (UDP)
    page.get_by_label("IP (UDP)").click()
    expect(page.locator("#ipFields")).to_be_visible()
    expect(page.locator("#serialFields")).not_to_be_visible()

def test_chat_panel_toggle(page: Page, server: str):
    page.goto(server)
    
    # Chat should be visible by default or toggleable
    page.locator("#toggleChat").click()
    # Check if collapsed class is toggled or width changes
    # Based on style.css sidebar/chat toggle logic
    expect(page.locator("#chat-container")).not_to_be_visible()
    
    page.locator("#toggleChat").click()
    expect(page.locator("#chat-container")).to_be_visible()
