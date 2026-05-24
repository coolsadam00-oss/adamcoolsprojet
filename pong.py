import turtle

# Screen setup
WIDTH, HEIGHT = 800, 600
screen = turtle.Screen()
screen.title("Pong")
screen.bgcolor("black")
screen.setup(width=WIDTH, height=HEIGHT)
screen.tracer(0)

# Left paddle
left_paddle = turtle.Turtle()
left_paddle.speed(0)
left_paddle.shape("square")
left_paddle.color("white")
left_paddle.shapesize(stretch_wid=5, stretch_len=1)
left_paddle.penup()
left_paddle.goto(-350, 0)

# Right paddle
right_paddle = turtle.Turtle()
right_paddle.speed(0)
right_paddle.shape("square")
right_paddle.color("white")
right_paddle.shapesize(stretch_wid=5, stretch_len=1)
right_paddle.penup()
right_paddle.goto(350, 0)

# Ball
ball = turtle.Turtle()
ball.speed(0)
ball.shape("circle")
ball.color("white")
ball.penup()
ball.goto(0, 0)
ball.dx = 0.18
ball.dy = 0.18

# Score
score_left = 0
score_right = 0

score_display = turtle.Turtle()
score_display.speed(0)
score_display.color("white")
score_display.penup()
score_display.hideturtle()
score_display.goto(0, HEIGHT // 2 - 40)
score_display.write("0  :  0", align="center", font=("Courier", 24, "normal"))

# Paddle movement
PADDLE_MOVE = 20


def left_paddle_up():
    y = left_paddle.ycor() + PADDLE_MOVE
    if y > HEIGHT / 2 - 50:
        y = HEIGHT / 2 - 50
    left_paddle.sety(y)


def left_paddle_down():
    y = left_paddle.ycor() - PADDLE_MOVE
    if y < -HEIGHT / 2 + 50:
        y = -HEIGHT / 2 + 50
    left_paddle.sety(y)


def right_paddle_up():
    y = right_paddle.ycor() + PADDLE_MOVE
    if y > HEIGHT / 2 - 50:
        y = HEIGHT / 2 - 50
    right_paddle.sety(y)


def right_paddle_down():
    y = right_paddle.ycor() - PADDLE_MOVE
    if y < -HEIGHT / 2 + 50:
        y = -HEIGHT / 2 + 50
    right_paddle.sety(y)

# Keyboard binding
screen.listen()
screen.onkey(left_paddle_up, "w")
screen.onkey(left_paddle_down, "s")
screen.onkey(right_paddle_up, "Up")
screen.onkey(right_paddle_down, "Down")

# Main loop
while True:
    screen.update()

    ball.setx(ball.xcor() + ball.dx)
    ball.sety(ball.ycor() + ball.dy)

    # Border collision
    if ball.ycor() > HEIGHT / 2 - 10:
        ball.sety(HEIGHT / 2 - 10)
        ball.dy *= -1

    if ball.ycor() < -HEIGHT / 2 + 10:
        ball.sety(-HEIGHT / 2 + 10)
        ball.dy *= -1

    if ball.xcor() > WIDTH / 2 - 10:
        score_left += 1
        score_display.clear()
        score_display.write(f"{score_left}  :  {score_right}", align="center", font=("Courier", 24, "normal"))
        ball.goto(0, 0)
        ball.dx = -0.18

    if ball.xcor() < -WIDTH / 2 + 10:
        score_right += 1
        score_display.clear()
        score_display.write(f"{score_left}  :  {score_right}", align="center", font=("Courier", 24, "normal"))
        ball.goto(0, 0)
        ball.dx = 0.18

    # Paddle collision
    if (ball.xcor() > 340 and ball.xcor() < 350) and (ball.ycor() < right_paddle.ycor() + 50 and ball.ycor() > right_paddle.ycor() - 50):
        ball.setx(340)
        ball.dx *= -1.05

    if (ball.xcor() < -340 and ball.xcor() > -350) and (ball.ycor() < left_paddle.ycor() + 50 and ball.ycor() > left_paddle.ycor() - 50):
        ball.setx(-340)
        ball.dx *= -1.05
