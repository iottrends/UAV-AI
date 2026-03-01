from playwright.sync_api import Page, expect


def test_param_audit_panel_shows_error_when_no_params(page: Page, server: str):
    page.goto(server)

    # Navigate to Parameters tab
    page.get_by_text("Parameters").click()
    expect(page.locator("#parameters-tab")).to_be_visible()

    # Run audit
    page.locator("#runAuditBtn").click()

    # Panel should appear with error message when no params are loaded
    expect(page.locator("#auditPanel")).to_be_visible()
    expect(page.locator("#auditSummary")).to_contain_text("No parameters loaded")

    # Toggle audit off
    page.locator("#runAuditBtn").click()
    expect(page.locator("#auditPanel")).not_to_be_visible()
