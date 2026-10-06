/* Rasterize the bundled, unchanged Ooptra SVG for AstrBot's logo.png convention.
 * node tools/render_plugin_logo.cjs /path/to/node_modules/playwright
 */
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.argv[2] || 'playwright');
(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage();
    const svg = fs.readFileSync(path.join(__dirname, '../pages/control/logo.svg'), 'utf8');
    const png = await page.evaluate(async source => {
      const image = new Image();
      image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(source);
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 512;
      canvas.getContext('2d').drawImage(image, 0, 0, 512, 512);
      return canvas.toDataURL('image/png').split(',')[1];
    }, svg);
    fs.writeFileSync(path.join(__dirname, '../logo.png'), Buffer.from(png, 'base64'));
    console.log('Generated logo.png: original Ooptra mark, 512 x 512, transparent background.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
