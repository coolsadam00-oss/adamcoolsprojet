import random
import time

print("=== Russian Roulette ===")
print("Try to survive as many rounds as possible.")
print("Type 'spin' to spin the cylinder, or 'quit' to exit.")

round_number = 0
lives = 1

while True:
    command = input("\nEnter command (spin/quit): ").strip().lower()
    if command == "quit":
        print("Goodbye! Stay safe.")
        break
    if command != "spin":
        print("Please type 'spin' or 'quit'.")
        continue

    round_number += 1
    print(f"Round {round_number}: spinning the cylinder...")
    time.sleep(1)

    bullet_position = random.randint(1, 6)
    chamber = random.randint(1, 6)
    if bullet_position == chamber:
        print("Bang! You lost this round.")
        print(f"You survived {round_number - 1} full rounds.")
        break
    else:
        print("Click. You survived this round.")
        print(f"Rounds survived: {round_number}")
        if round_number % 5 == 0:
            print("Nice! Keep going.")

print("Game over.")
