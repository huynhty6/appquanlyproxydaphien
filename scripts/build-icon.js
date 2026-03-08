const sharp = require('sharp');
const path = require('path');
const fs = require('fs');

const appDir = path.join(__dirname, '..', 'app');
const svgPath = path.join(appDir, 'icon.svg');
const pngPath = path.join(appDir, 'icon.png');
const pngDarkPath = path.join(appDir, 'icon-dark.png');

const SIZE = 256;
const LOGO_W = 200;
const RADIUS = 32;

if (!fs.existsSync(svgPath)) {
  console.log('icon.svg not found, skipping');
  process.exit(0);
}

const roundMask = Buffer.from(
  `<svg width="${SIZE}" height="${SIZE}">
    <rect x="0" y="0" width="${SIZE}" height="${SIZE}" rx="${RADIUS}" ry="${RADIUS}" fill="white"/>
  </svg>`
);

async function buildIcon(bg, outPath) {
  let logoBuf = await sharp(svgPath)
    .resize(LOGO_W, null)
    .flatten({ background: bg })
    .png()
    .toBuffer();

  let meta = await sharp(logoBuf).metadata();

  if (meta.height > SIZE) {
    const cropTop = Math.round((meta.height - SIZE) / 2);
    logoBuf = await sharp(logoBuf)
      .extract({ left: 0, top: cropTop, width: meta.width, height: SIZE })
      .png()
      .toBuffer();
    meta = await sharp(logoBuf).metadata();
  }

  const left = Math.round((SIZE - meta.width) / 2);
  const top = Math.round((SIZE - meta.height) / 2);

  const canvas = await sharp({
    create: { width: SIZE, height: SIZE, channels: 4, background: bg }
  })
    .composite([{ input: logoBuf, left, top }])
    .png()
    .toBuffer();

  await sharp(canvas)
    .composite([{ input: roundMask, blend: 'dest-in' }])
    .png()
    .toFile(outPath);
}

Promise.all([
  buildIcon('#1349ec', pngPath),
  buildIcon('#101522', pngDarkPath),
])
  .then(() => console.log('icon.png, icon-dark.png created'))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
