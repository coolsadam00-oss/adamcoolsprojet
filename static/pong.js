const canvas = document.getElementById('pongCanvas');
const ctx = canvas.getContext('2d');

const paddleWidth = 16;
const paddleHeight = 100;
const ballSize = 14;
const speed = 6;
const botSpeed = 4.2;

let playerY = canvas.height / 2 - paddleHeight / 2;
let botY = canvas.height / 2 - paddleHeight / 2;
let ballX = canvas.width / 2 - ballSize / 2;
let ballY = canvas.height / 2 - ballSize / 2;
let ballDX = speed;
let ballDY = speed;
let playerScore = 0;
let botScore = 0;
let playing = true;
let upPressed = false;
let downPressed = false;

function resetGame() {
  playerY = canvas.height / 2 - paddleHeight / 2;
  botY = canvas.height / 2 - paddleHeight / 2;
  ballX = canvas.width / 2 - ballSize / 2;
  ballY = canvas.height / 2 - ballSize / 2;
  ballDX = speed * (Math.random() > 0.5 ? 1 : -1);
  ballDY = speed * (Math.random() > 0.5 ? 1 : -1);
  playing = true;
}

function drawRect(x, y, w, h, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x, y, w, h);
}

function drawBall() {
  drawRect(ballX, ballY, ballSize, ballSize, '#ffffff');
}

function drawNet() {
  for (let i = 0; i < canvas.height; i += 30) {
    drawRect(canvas.width / 2 - 1, i, 2, 20, '#4a90e2');
  }
}

function drawScores() {
  ctx.fillStyle = '#fff';
  ctx.font = '28px Arial';
  ctx.textAlign = 'center';
  ctx.fillText(playerScore, canvas.width * 0.25, 40);
  ctx.fillText(botScore, canvas.width * 0.75, 40);
}

function drawGameOver() {
  ctx.fillStyle = '#fff';
  ctx.font = '32px Arial';
  ctx.textAlign = 'center';
  ctx.fillText('Game over! Press Space to restart', canvas.width / 2, canvas.height / 2);
}

function update() {
  if (!playing) return;

  if (upPressed) {
    playerY -= speed;
  }
  if (downPressed) {
    playerY += speed;
  }
  playerY = Math.max(0, Math.min(canvas.height - paddleHeight, playerY));

  // Bot movement
  const targetY = ballY - paddleHeight / 2 + ballSize / 2;
  if (botY + paddleHeight / 2 < targetY - 10) {
    botY += botSpeed;
  } else if (botY + paddleHeight / 2 > targetY + 10) {
    botY -= botSpeed;
  }
  botY = Math.max(0, Math.min(canvas.height - paddleHeight, botY));

  ballX += ballDX;
  ballY += ballDY;

  if (ballY <= 0 || ballY + ballSize >= canvas.height) {
    ballDY *= -1;
  }

  if (ballX <= paddleWidth) {
    if (ballY + ballSize > playerY && ballY < playerY + paddleHeight) {
      ballDX *= -1.1;
      ballX = paddleWidth;
    }
  }

  if (ballX + ballSize >= canvas.width - paddleWidth) {
    if (ballY + ballSize > botY && ballY < botY + paddleHeight) {
      ballDX *= -1.1;
      ballX = canvas.width - paddleWidth - ballSize;
    }
  }

  if (ballX < 0) {
    botScore += 1;
    playing = false;
  }

  if (ballX + ballSize > canvas.width) {
    playerScore += 1;
    playing = false;
  }
}

function draw() {
  ctx.fillStyle = '#05050d';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawNet();
  drawRect(0, playerY, paddleWidth, paddleHeight, '#fff');
  drawRect(canvas.width - paddleWidth, botY, paddleWidth, paddleHeight, '#fff');
  drawBall();
  drawScores();
  if (!playing) drawGameOver();
}

function loop() {
  update();
  draw();
  requestAnimationFrame(loop);
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'ArrowUp' || event.key.toLowerCase() === 'w') {
    upPressed = true;
  }
  if (event.key === 'ArrowDown' || event.key.toLowerCase() === 's') {
    downPressed = true;
  }
  if (event.key === ' ' && !playing) {
    resetGame();
  }
});

document.addEventListener('keyup', (event) => {
  if (event.key === 'ArrowUp' || event.key.toLowerCase() === 'w') {
    upPressed = false;
  }
  if (event.key === 'ArrowDown' || event.key.toLowerCase() === 's') {
    downPressed = false;
  }
});

resetGame();
loop();
