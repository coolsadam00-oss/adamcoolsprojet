import random
import turtle
import time

WIDTH, HEIGHT = 600, 600
PLAYER_SPEED = 20
OBSTACLE_SPEED_START = 5
OBSTACLE_ACCELERATION = 0.1
OBSTACLE_SIZE = 20
SPAWN_DELAY = 600  # milliseconds

screen = turtle.Screen()
screen.setup(WIDTH, HEIGHT)
screen.title("Fast Dodge Game")
screen.bgcolor("black")
screen.tracer(0)

player = turtle.Turtle()
player.shape("square")
player.color("white")
player.shapesize(stretch_wid=1, stretch_len=2)
player.penup()
player.goto(0, -HEIGHT // 2 + 50)

score = 0
speed = OBSTACLE_SPEED_START
obstacles = []

def move_left():
    x = player.xcor() - PLAYER_SPEED
    if x < -WIDTH // 2 + 20:
        x = -WIDTH // 2 + 20
    player.setx(x)


def move_right():
    x = player.xcor() + PLAYER_SPEED
    if x > WIDTH // 2 - 20:
        x = WIDTH // 2 - 20
    player.setx(x)


def create_obstacle():
    obstacle = turtle.Turtle()
    obstacle.shape("square")
    obstacle.color("red")
    obstacle.shapesize(stretch_wid=1, stretch_len=2)
    obstacle.penup()
    x = random.randint(-WIDTH // 2 + 30, WIDTH // 2 - 30)
    obstacle.goto(x, HEIGHT // 2 + 20)
    obstacles.append(obstacle)
    screen.ontimer(create_obstacle, SPAWN_DELAY)


def update_score():
    global score
    score += 1
    score_text.clear()
    score_text.write(f"Score: {score}", align="center", font=("Arial", 18, "normal"))


def game_over():
    screen.clear()
    screen.bgcolor("black")
    game_over_text = turtle.Turtle()
    game_over_text.hideturtle()
    game_over_text.color("white")
    game_over_text.penup()
    game_over_text.goto(0, 0)
    game_over_text.write(
        f"Game Over\nFinal Score: {score}",
        align="center",
        font=("Arial", 24, "bold"),
    )
    screen.update()
    time.sleep(3)
    screen.bye()


def update():
    global speed
    for obstacle in obstacles:
        obstacle.sety(obstacle.ycor() - speed)
        if obstacle.ycor() < -HEIGHT // 2 - 20:
            obstacle.hideturtle()
            obstacles.remove(obstacle)
            update_score()
            speed += OBSTACLE_ACCELERATION
        if obstacle.distance(player) < 25:
            game_over()
            return

    screen.update()
    screen.ontimer(update, 20)

score_text = turtle.Turtle()
score_text.hideturtle()
score_text.color("white")
score_text.penup()
score_text.goto(0, HEIGHT // 2 - 40)
score_text.write(f"Score: {score}", align="center", font=("Arial", 18, "normal"))

screen.listen()
screen.onkey(move_left, "Left")
screen.onkey(move_right, "Right")
screen.onkey(move_left, "a")
screen.onkey(move_right, "d")

create_obstacle()
update()

screen.mainloop()
