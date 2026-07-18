const { chromium } = require("playwright");

const documentPaths = process.argv.slice(2);
if (documentPaths.length !== 2) {
  throw new Error("usage: node document_mobile_overflow.cjs REPORT_PATH CERTIFICATE_PATH");
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/usr/bin/google-chrome",
  });
  try {
    for (const documentPath of documentPaths) {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
      await page.goto(`file://${documentPath}`);
      const dimensions = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        viewportWidth: document.documentElement.clientWidth,
      }));
      if (dimensions.scrollWidth > dimensions.viewportWidth) {
        throw new Error(
          `${documentPath}: document scrollWidth ${dimensions.scrollWidth} exceeds viewport ${dimensions.viewportWidth}`,
        );
      }
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
