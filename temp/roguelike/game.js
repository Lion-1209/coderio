// ==================== CONFIGURATION ====================
const CONFIG = {
  TILE_SIZE: 16,
  MAP_WIDTH: 80,
  MAP_HEIGHT: 60,
  VIEWPORT_TILES_X: 25,
  VIEWPORT_TILES_Y: 19,
  ROOM_MIN_SIZE: 5,
  ROOM_MAX_SIZE: 10,
  MAX_ROOMS: 12,
  MAX_ENEMIES_PER_FLOOR: 8,
  MAX_ITEMS_PER_FLOOR: 5,
  PLAYER_HP: 20,
  PLAYER_ATK: 2,
  PLAYER_DEF: 0,
  PLAYER_SPD: 1.0,
  ENEMY_TYPES: [
    { name: 'Goblin', hp: 6, atk: 2, def: 0, spd: 1.0, color: '#27ae60', xp: 10 },
    { name: 'Skeleton', hp: 10, atk: 3, def: 1, spd: 0.9, color: '#bdc3c7', xp: 20 },
    { name: 'Orc', hp: 15, atk: 4, def: 2, spd: 0.8, color: '#e67e22', xp: 35 },
    { name: 'Demon', hp: 25, atk: 6, def: 3, spd: 1.1, color: '#8e44ad', xp: 60 },
  ],
  ITEM_TYPES: [
    { name: 'Health Potion', type: 'consumable', color: '#e74c3c', effect: { hp: 8 } },
    { name: 'Sword', type: 'weapon', color: '#95a5a6', effect: { atk: 2 } },
    { name: 'Shield', type: 'armor', color: '#7f8c8d', effect: { def: 1 } },
    { name: 'Boots', type: 'accessory', color: '#d35400', effect: { spd: 0.2 } },
  ]
};

// ==================== TILE TYPES ====================
const TILE = {
  WALL: 0,
  FLOOR: 1,
  DOOR: 2,
  STAIRS: 3
};

const TILE_CHARS = {
  [TILE.WALL]: '█',
  [TILE.FLOOR]: '·',
  [TILE.DOOR]: '+',
  [TILE.STAIRS]: '>'
};

const TILE_COLORS = {
  [TILE.WALL]: '#1a1a2e',
  [TILE.FLOOR]: '#16213e',
  [TILE.DOOR]: '#8b4513',
  [TILE.STAIRS]: '#f1c40f'
};

// ==================== GAME STATE ====================
const GameState = {
  MENU: 'menu',
  PLAYING: 'playing',
  GAME_OVER: 'gameOver'
};

let canvas, ctx;
let gameState = GameState.MENU;
let currentFloor = 1;
let kills = 0;
let map = [];
let rooms = [];
let player = null;
let enemies = [];
let items = [];
let particles = [];
let messages = [];
let attackCooldown = 0;
let lastTime = 0;
let camera = { x: 0, y: 0 };

// ==================== INITIALIZATION ====================
function init() {
  canvas = document.getElementById('gameCanvas');
  ctx = canvas.getContext('2d');
  resize();
  window.addEventListener('resize', resize);
  window.addEventListener('keydown', handleInput);
  requestAnimationFrame(gameLoop);
}

function resize() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}

// ==================== INPUT HANDLING ====================
function handleInput(e) {
  if (gameState !== GameState.PLAYING) return;
  
  const key = e.key.toLowerCase();
  let dx = 0, dy = 0;
  
  if (key === 'w' || key === 'arrowup') dy = -1;
  else if (key === 's' || key === 'arrowdown') dy = 1;
  else if (key === 'a' || key === 'arrowleft') dx = -1;
  else if (key === 'd' || key === 'arrowright') dx = 1;
  else if (key === ' ') { waitTurn(); e.preventDefault(); return; }
  else if (key === 'g' || key === 'e') { pickupItem(); return; }
  else return;
  
  e.preventDefault();
  movePlayer(dx, dy);
}

// ==================== GAME LOOP ====================
function gameLoop(timestamp) {
  const dt = timestamp - lastTime;
  lastTime = timestamp;
  
  update(dt);
  render();
  updateUI();
  
  requestAnimationFrame(gameLoop);
}

function update(dt) {
  if (gameState !== GameState.PLAYING) return;
  
  if (attackCooldown > 0) attackCooldown -= dt;
  
  // Update particles
  particles = particles.filter(p => {
    p.life -= dt;
    p.x += p.vx * dt * 0.01;
    p.y += p.vy * dt * 0.01;
    return p.life > 0;
  });
  
  // Update enemies
  enemies.forEach(enemy => {
    if (enemy.hp <= 0) return;
    
    const dist = Math.hypot(player.x - enemy.x, player.y - enemy.y);
    
    // Simple AI: move toward player if in range
    if (dist < 8) {
      const angle = Math.atan2(player.y - enemy.y, player.x - enemy.x);
      const moveSpeed = enemy.spd * 0.05 * dt;
      const nx = Math.round(enemy.x + Math.cos(angle) * moveSpeed);
      const ny = Math.round(enemy.y + Math.sin(angle) * moveSpeed);
      
      if (isWalkable(nx, ny) && !(nx === player.x && ny === player.y)) {
        enemy.x = nx;
        enemy.y = ny;
      } else if (nx === player.x && ny === player.y) {
        attackEnemy(enemy);
      }
    }
  });
  
  updateCamera();
}

function render() {
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  
  if (gameState === GameState.MENU || gameState === GameState.GAME_OVER) return;
  
  const tileSize = CONFIG.TILE_SIZE;
  const viewportW = Math.ceil(canvas.width / tileSize) + 2;
  const viewportH = Math.ceil(canvas.height / tileSize) + 2;
  
  const startX = Math.max(0, Math.floor(camera.x) - 1);
  const startY = Math.max(0, Math.floor(camera.y) - 1);
  const endX = Math.min(CONFIG.MAP_WIDTH, startX + viewportW);
  const endY = Math.min(CONFIG.MAP_HEIGHT, startY + viewportH);
  
  // Draw tiles
  for (let y = startY; y < endY; y++) {
    for (let x = startX; x < endX; x++) {
      const tile = map[y]?.[x];
      if (!tile) continue;
      
      const screenX = (x - camera.x) * tileSize;
      const screenY = (y - camera.y) * tileSize;
      
      ctx.fillStyle = TILE_COLORS[tile] || '#000';
      ctx.fillRect(screenX, screenY, tileSize, tileSize);
      
      // Draw tile character
      if (tile === TILE.FLOOR) {
        ctx.fillStyle = '#2c3e50';
        ctx.fillRect(screenX + 1, screenY + 1, tileSize - 2, tileSize - 2);
      }
    }
  }
  
  // Draw items
  items.forEach(item => {
    if (isVisible(item.x, item.y)) {
      const screenX = (item.x - camera.x) * tileSize;
      const screenY = (item.y - camera.y) * tileSize;
      drawEntity(screenX, screenY, item.symbol || '?', item.color || '#fff', tileSize);
    }
  });
  
  // Draw stairs
  if (stairs) {
    const screenX = (stairs.x - camera.x) * tileSize;
    const screenY = (stairs.y - camera.y) * tileSize;
    drawEntity(screenX, screenY, '>', '#f1c40f', tileSize, true);
  }
  
  // Draw enemies
  enemies.forEach(enemy => {
    if (enemy.hp <= 0) return;
    if (isVisible(enemy.x, enemy.y)) {
      const screenX = (enemy.x - camera.x) * tileSize;
      const screenY = (enemy.y - camera.y) * tileSize;
      drawEntity(screenX, screenY, enemy.symbol, enemy.color, tileSize);
      
      // HP bar
      if (enemy.hp < enemy.maxHp) {
        const barW = tileSize;
        const barH = 3;
        const hpPercent = enemy.hp / enemy.maxHp;
        ctx.fillStyle = '#333';
        ctx.fillRect(screenX, screenY - 5, barW, barH);
        ctx.fillStyle = hpPercent > 0.5 ? '#2ecc71' : hpPercent > 0.25 ? '#f39c12' : '#e74c3c';
        ctx.fillRect(screenX, screenY - 5, barW * hpPercent, barH);
      }
    }
  });
  
  // Draw player
  const playerScreenX = (player.x - camera.x) * tileSize;
  const playerScreenY = (player.y - camera.y) * tileSize;
  drawEntity(playerScreenX, playerScreenY, '@', '#3498db', tileSize, true);
  
  // Draw particles
  particles.forEach(p => {
    const screenX = (p.x - camera.x) * tileSize;
    const screenY = (p.y - camera.y) * tileSize;
    ctx.globalAlpha = p.life / p.maxLife;
    ctx.fillStyle = p.color;
    ctx.fillRect(screenX, screenY, 3, 3);
    ctx.globalAlpha = 1;
  });
  
  // Draw message log
  renderLog();
}

function drawEntity(x, y, symbol, color, size, glow = false) {
  ctx.font = `bold ${size}px Courier New`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  
  if (glow) {
    ctx.shadowColor = color;
    ctx.shadowBlur = 8;
  }
  
  ctx.fillStyle = color;
  ctx.fillText(symbol, x + size/2, y + size/2);
  
  ctx.shadowBlur = 0;
}

function renderLog() {
  const logEl = document.getElementById('log');
  const visible = messages.slice(-6);
  logEl.innerHTML = visible.map(m => 
    `<div class="log-entry ${m.class || 'msg-info'}">${m.text}</div>`
  ).join('');
  logEl.scrollTop = logEl.scrollHeight;
}

function updateUI() {
  if (!player) return;
  
  document.getElementById('hpText').textContent = `${player.hp}/${player.maxHp}`;
  document.getElementById('hpBar').style.width = `${(player.hp / player.maxHp) * 100}%`;
  document.getElementById('atkText').textContent = player.atk;
  document.getElementById('defText').textContent = player.def;
  document.getElementById('spdText').textContent = player.spd.toFixed(1);
  document.getElementById('floorText').textContent = currentFloor;
  document.getElementById('killsText').textContent = kills;
}

function updateCamera() {
  const viewportTilesX = Math.ceil(canvas.width / CONFIG.TILE_SIZE);
  const viewportTilesY = Math.ceil(canvas.height / CONFIG.TILE_SIZE);

  camera.x = player.x - viewportTilesX / 2;
  camera.y = player.y - viewportTilesY / 2;

  camera.x = Math.max(0, Math.min(CONFIG.MAP_WIDTH - viewportTilesX, camera.x));
  camera.y = Math.max(0, Math.min(CONFIG.MAP_HEIGHT - viewportTilesY, camera.y));
}

function isVisible(x, y) {
  const ts = CONFIG.TILE_SIZE;
  const sx = (x - camera.x) * ts;
  const sy = (y - camera.y) * ts;
  return sx >= -ts && sx < canvas.width + ts && sy >= -ts && sy < canvas.height + ts;
}

// ==================== MAP GENERATION ====================
function generateMap() {
  map = Array(CONFIG.MAP_HEIGHT).fill(null).map(() => Array(CONFIG.MAP_WIDTH).fill(TILE.WALL));
  rooms = [];
  enemies = [];
  items = [];
  particles = [];
  messages = [];
  
  // Generate rooms
  for (let i = 0; i < CONFIG.MAX_ROOMS; i++) {
    const w = randomInt(CONFIG.ROOM_MIN_SIZE, CONFIG.ROOM_MAX_SIZE);
    const h = randomInt(CONFIG.ROOM_MIN_SIZE, CONFIG.ROOM_MAX_SIZE);
    const x = randomInt(1, CONFIG.MAP_WIDTH - w - 1);
    const y = randomInt(1, CONFIG.MAP_HEIGHT - h - 1);
    
    const newRoom = { x, y, w, h, cx: Math.floor(x + w/2), cy: Math.floor(y + h/2) };
    
    let overlaps = false;
    for (const other of rooms) {
      if (x <= other.x + other.w + 1 && x + w + 1 >= other.x &&
          y <= other.y + other.h + 1 && y + h + 1 >= other.y) {
        overlaps = true;
        break;
      }
    }
    
    if (!overlaps) {
      carveRoom(newRoom);
      
      if (rooms.length > 0) {
        const prev = rooms[rooms.length - 1];
        carveCorridor(prev.cx, prev.cy, newRoom.cx, newRoom.cy);
      }
      
      rooms.push(newRoom);
    }
  }
  
  // Place player in first room
  const startRoom = rooms[0];
  player.x = startRoom.cx;
  player.y = startRoom.cy;
  
  // Place stairs in last room
  const lastRoom = rooms[rooms.length - 1];
  stairs = { x: lastRoom.cx, y: lastRoom.cy };
  map[stairs.y][stairs.x] = TILE.STAIRS;
  
  // Place enemies
  const enemyCount = Math.min(CONFIG.MAX_ENEMIES_PER_FLOOR + Math.floor(currentFloor / 2), 15);
  for (let i = 0; i < enemyCount; i++) {
    const room = rooms[Math.floor(Math.random() * rooms.length)];
    const ex = randomInt(room.x + 1, room.x + room.w - 2);
    const ey = randomInt(room.y + 1, room.y + room.h - 2);
    
    if (isWalkable(ex, ey) && !(ex === player.x && ey === player.y)) {
      spawnEnemy(ex, ey);
    }
  }
  
  // Place items
  const itemCount = Math.min(CONFIG.MAX_ITEMS_PER_FLOOR + Math.floor(currentFloor / 3), 8);
  for (let i = 0; i < itemCount; i++) {
    const room = rooms[Math.floor(Math.random() * rooms.length)];
    const ix = randomInt(room.x + 1, room.x + room.w - 2);
    const iy = randomInt(room.y + 1, room.y + room.h - 2);
    
    if (isWalkable(ix, iy)) {
      spawnItem(ix, iy);
    }
  }
  
  addMessage(`Floor ${currentFloor} entered. Find the stairs (>).`, 'msg-floor');
}

function carveRoom(room) {
  for (let y = room.y; y < room.y + room.h; y++) {
    for (let x = room.x; x < room.x + room.w; x++) {
      map[y][x] = TILE.FLOOR;
    }
  }
}

function carveCorridor(x1, y1, x2, y2) {
  let x = x1, y = y1;
  
  // Horizontal first, then vertical
  while (x !== x2) {
    map[y][x] = TILE.FLOOR;
    x += x < x2 ? 1 : -1;
  }
  while (y !== y2) {
    map[y][x] = TILE.FLOOR;
    y += y < y2 ? 1 : -1;
  }
  map[y2][x2] = TILE.FLOOR;
}

// ==================== ENTITY MANAGEMENT ====================
function spawnEnemy(x, y) {
  const typeIndex = Math.min(
    Math.floor(Math.random() * (1 + Math.floor(currentFloor / 2))),
    CONFIG.ENEMY_TYPES.length - 1
  );
  const type = CONFIG.ENEMY_TYPES[typeIndex];
  const scale = 1 + (currentFloor - 1) * 0.15;
  
  enemies.push({
    x, y,
    name: type.name,
    hp: Math.floor(type.hp * scale),
    maxHp: Math.floor(type.hp * scale),
    atk: Math.floor(type.atk * scale),
    def: Math.floor(type.def * scale),
    spd: type.spd,
    color: type.color,
    symbol: type.name[0],
    xp: type.xp
  });
}

function spawnItem(x, y) {
  const typeIndex = Math.floor(Math.random() * CONFIG.ITEM_TYPES.length);
  const type = CONFIG.ITEM_TYPES[typeIndex];
  items.push({
    x, y,
    name: type.name,
    type: type.type,
    color: type.color,
    effect: type.effect,
    symbol: type.type === 'consumable' ? '!' : type.type === 'weapon' ? '/' : type.type === 'armor' ? ']' : '*'
  });
}

// ==================== PLAYER ACTIONS ====================
function movePlayer(dx, dy) {
  if (!player || attackCooldown > 0) return;
  
  const nx = player.x + dx;
  const ny = player.y + dy;
  
  // Check bounds
  if (nx < 0 || nx >= CONFIG.MAP_WIDTH || ny < 0 || ny >= CONFIG.MAP_HEIGHT) return;
  
  // Check wall collision
  if (map[ny][nx] === TILE.WALL) return;
  
  // Check enemy collision (attack)
  const enemy = enemies.find(e => e.x === nx && e.y === ny && e.hp > 0);
  if (enemy) {
    attackEnemy(enemy);
    attackCooldown = 300;
    return;
  }
  
  // Move
  player.x = nx;
  player.y = ny;
  attackCooldown = 100;
  
  // Check for stairs
  if (map[ny][nx] === TILE.STAIRS) {
    nextFloor();
  }
}

function waitTurn() {
  if (attackCooldown > 0) return;
  attackCooldown = 200;
}

function attackEnemy(enemy) {
  const damage = Math.max(1, player.atk - enemy.def + Math.floor(Math.random() * player.atk));
  enemy.hp -= damage;
  
  spawnParticles(enemy.x, enemy.y, '#e74c3c', 5);
  addMessage(`You hit ${enemy.name} for ${damage} damage.`, 'msg-damage');
  
  if (enemy.hp <= 0) {
    addMessage(`${enemy.name} defeated!`, 'msg-info');
    kills++;
    
    // Drop item chance
    if (Math.random() < 0.3) {
      spawnItem(enemy.x, enemy.y);
    }
  }
  
  // Enemy counter-attack
  if (enemy.hp > 0) {
    setTimeout(() => {
      const enemyDamage = Math.max(1, enemy.atk - player.def + Math.floor(Math.random() * enemy.atk));
      player.hp -= enemyDamage;
      spawnParticles(player.x, player.y, '#e74c3c', 3);
      addMessage(`${enemy.name} hits you for ${enemyDamage} damage!`, 'msg-damage');
      
      if (player.hp <= 0) {
        gameOver();
      }
    }, 100);
  }
}

function pickupItem() {
  const itemIndex = items.findIndex(i => i.x === player.x && i.y === player.y);
  if (itemIndex === -1) return;
  
  const item = items[itemIndex];
  
  if (item.type === 'consumable') {
    const heal = item.effect.hp;
    player.hp = Math.min(player.maxHp, player.hp + heal);
    addMessage(`Used ${item.name}. Healed ${heal} HP.`, 'msg-heal');
  } else {
    if (item.effect.atk) { player.atk += item.effect.atk; addMessage(`Picked up ${item.name}. ATK +${item.effect.atk}`, 'msg-item'); }
    if (item.effect.def) { player.def += item.effect.def; addMessage(`Picked up ${item.name}. DEF +${item.effect.def}`, 'msg-item'); }
    if (item.effect.spd) { player.spd += item.effect.spd; addMessage(`Picked up ${item.name}. SPD +${item.effect.spd}`, 'msg-item'); }
  }
  
  spawnParticles(player.x, player.y, item.color, 8);
  items.splice(itemIndex, 1);
}

function nextFloor() {
  currentFloor++;
  addMessage(`Descending to floor ${currentFloor}...`, 'msg-floor');
  
  // Increase player stats slightly
  player.maxHp += 2;
  player.hp = player.maxHp;
  
  generateMap();
}

// ==================== PARTICLES ====================
function spawnParticles(x, y, color, count) {
  for (let i = 0; i < count; i++) {
    particles.push({
      x: x + 0.5,
      y: y + 0.5,
      vx: (Math.random() - 0.5) * 2,
      vy: (Math.random() - 0.5) * 2,
      color,
      life: 300 + Math.random() * 200,
      maxLife: 500
    });
  }
}

// ==================== MESSAGES ====================
function addMessage(text, className = 'msg-info') {
  messages.push({ text, class: className });
  if (messages.length > 50) messages.shift();
}

// ==================== UTILITY ====================
function isWalkable(x, y) {
  return x >= 0 && x < CONFIG.MAP_WIDTH && y >= 0 && y < CONFIG.MAP_HEIGHT && 
         map[y][x] !== TILE.WALL && map[y][x] !== TILE.DOOR;
}

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

// ==================== GAME FLOW ====================
function startGame() {
  document.getElementById('screen').style.display = 'none';
  document.getElementById('ui').style.display = 'block';
  
  currentFloor = 1;
  kills = 0;
  
  player = {
    x: 0, y: 0,
    hp: CONFIG.PLAYER_HP,
    maxHp: CONFIG.PLAYER_HP,
    atk: CONFIG.PLAYER_ATK,
    def: CONFIG.PLAYER_DEF,
    spd: CONFIG.PLAYER_SPD
  };
  
  generateMap();
  gameState = GameState.PLAYING;
}

function gameOver() {
  gameState = GameState.GAME_OVER;
  document.getElementById('ui').style.display = 'none';
  document.getElementById('screen').style.display = 'flex';
  document.querySelector('.screen-title').textContent = 'GAME OVER';
  document.querySelector('.screen-title').style.color = '#7f0000';
  document.querySelector('.screen-subtitle').textContent = 
    `You reached floor ${currentFloor} and killed ${kills} enemies.`;
  document.querySelector('.btn').textContent = 'Try Again';
}

// ==================== START ====================
window.addEventListener('load', init);
