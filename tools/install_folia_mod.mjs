// Install the optional Folia plugin "歌词送去复查" (folia-mod/lyrics-review): a button in the
// player that sends the song being played to the review page. It is not in Folia's mod market,
// so it is copied into Folia's mods folder from here, together with a small kit.json telling it
// where these tools are and which port the review page uses.
//
// Usage: node install_folia_mod.mjs [--mods-dir <Folia's mods folder>] [--write]
// Without --write it only says what it would do. Afterwards the user has to enable the plugin
// in Folia's mods panel and accept its prompt: the plugin has a Node entry that starts the
// review server, and Folia asks before running an unsigned plugin's code.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { ROOT, REVIEW_PORT } from './kit-config.mjs';

const args = process.argv.slice(2), write = args.includes('--write');
const given = args.includes('--mods-dir') ? args[args.indexOf('--mods-dir') + 1] : null;
// where Electron keeps Folia's user data on each system
const userData = process.platform === 'win32' ? path.join(process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming'), 'Folia')
  : process.platform === 'darwin' ? path.join(os.homedir(), 'Library', 'Application Support', 'Folia')
    : path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'Folia');
const modsDir = path.resolve(given || path.join(userData, 'mods'));
const source = path.join(path.dirname(ROOT), 'folia-mod', 'lyrics-review'), target = path.join(modsDir, 'lyrics-review');
const manifest = JSON.parse(fs.readFileSync(path.join(source, 'mod.json'), 'utf8'));

if (!given && !fs.existsSync(userData)) {
  console.error(`没有找到 Folia 的数据目录（${userData}）。Folia 装了并且运行过吗？也可以用 --mods-dir 指定它的 mods 文件夹。`);
  process.exit(1);
}
const files = fs.readdirSync(source).filter(name => fs.statSync(path.join(source, name)).isFile());
console.log(`插件：${manifest.name} ${manifest.version}（声明支持的 Folia 版本：${manifest.folia}）`);
console.log(`安装到：${target}${fs.existsSync(target) ? '（会替换现有的同名插件）' : ''}`);
console.log(`文件：${files.join('、')}，另加 kit.json（工具目录 ${ROOT}，试听页端口 ${REVIEW_PORT}）`);
if (!write) { console.log('只是预览；加 --write 才会安装。'); process.exit(0); }

fs.rmSync(target, { recursive: true, force: true });
fs.mkdirSync(target, { recursive: true });
for (const name of files) fs.copyFileSync(path.join(source, name), path.join(target, name));
fs.writeFileSync(path.join(target, 'kit.json'), JSON.stringify({ tools: ROOT, port: REVIEW_PORT, node: process.execPath }, null, 2) + '\n', 'utf8');
console.log('已安装。接下来由用户在 Folia 里操作：打开模组面板，启用「' + manifest.name + '」并确认提示；Folia 开着的话先重新加载模组或重启。');
console.log('如果 Folia 的版本不在上面声明的范围内，它会拒绝加载这个插件；这时不要直接改范围，先确认 Folia 的内部接口没有变。');
