import random
import turtle
import time

# Full-screen setup
screen = turtle.Screen()
screen.title("Snake Full Screen")
screen.bgcolor("black")
screen.setup(width=1.0, height=1.0)
screen.tracer(0)

# Snake head
head = turtle.Turtle()
head.speed(0)
head.shape("square")
head.color("white")
head.penup()
head.goto(0, 0)
head.direction = "stop"

# Food
food = turtle.Turtle()
food.speed(0)
food.shape("circle")
food.color("red")
food.penup()
food.goto(0, 100)

segments = []

# Scoreboard
score = 0
high_score = 0

score_display = turtle.Turtle()
score_display.speed(0)
score_display.color("white")
score_display.penup()
score_display.hideturtle()
score_display.goto(0, screen.window_height() // 2 - 60)
score_display.write("Score: 0  High Score: 0", align="center", font=("Courier", 24, "normal"))

# Movement
MOVE_DISTANCE = 20

def go_up():
    if head.direction != "down":
        head.direction = "up"


def go_down():
    if head.direction != "up":
        head.direction = "down"


def go_left():
    if head.direction != "right":
        head.direction = "left"


def go_right():
    if head.direction != "left":
        head.direction = "right"


def move():
    x = head.xcor()
    y = head.ycor()
    if head.direction == "up":
        head.sety(y + MOVE_DISTANCE)
    if head.direction == "down":
        head.sety(y - MOVE_DISTANCE)
    if head.direction == "left":
        head.setx(x - MOVE_DISTANCE)
    if head.direction == "right":
        head.setx(x + MOVE_DISTANCE)

# Keyboard binding
screen.listen()
screen.onkey(go_up, "Up")
screen.onkey(go_down, "Down")
screen.onkey(go_left, "Left")
screen.onkey(go_right, "Right")

# Game loop
while True:
    screen.update()

    # Border collision
    half_width = screen.window_width() / 2 - 10
    half_height = screen.window_height() / 2 - 10

    if head.xcor() > half_width or head.xcor() < -half_width or head.ycor() > half_height or head.ycor() < -half_height:
        time.sleep(0.5)
        head.goto(0, 0)
        head.direction = "stop"

        for segment in segments:
            segment.goto(1000, 1000)
        segments.clear()

        score = 0
        score_display.clear()
        score_display.write(f"Score: {score}  High Score: {high_score}", align="center", font=("Courier", 24, "normal"))

    # Food collision
    if head.distance(food) < 20:
        x = random.randint(-int(half_width - 20), int(half_width - 20))
        y = random.randint(-int(half_height - 20), int(half_height - 20))
        food.goto(x - x % 20, y - y % 20)

        new_segment = turtle.Turtle()
        new_segment.speed(0)
        new_segment.shape("square")
        new_segment.color("green")
        new_segment.penup()
        segments.append(new_segment)

        score += 10
        if score > high_score:
            high_score = score

        score_display.clear()
        score_display.write(f"Score: {score}  High Score: {high_score}", align="center", font=("Courier", 24, "normal"))

    # Move body segments
    for index in range(len(segments) - 1, 0, -1):
        x = segments[index - 1].xcor()
        y = segments[index - 1].ycor()
        segments[index].goto(x, y)

    if len(segments) > 0:
        segments[0].goto(head.xcor(), head.ycor())

    move()

    # Self collision
    for segment in segments:
        if segment.distance(head) < 20:
            time.sleep(0.5)
            head.goto(0, 0)
            head.direction = "stop"

            for segment in segments:
                segment.goto(1000, 1000)
            segments.clear()

            score = 0
            score_display.clear()
            score_display.write(f"Score: {score}  High Score: {high_score}", align="center", font=("Courier", 24, "normal"))
            break

    time.sleep(0.1)
