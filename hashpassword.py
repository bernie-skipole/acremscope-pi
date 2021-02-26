
import hashlib


def hash_password(username, password):
    "Return hashed password, as a string, on failure return None"
    seed_password = username +  password
    hashed_password = hashlib.sha512(   seed_password.encode('utf-8')  ).hexdigest()
    return hashed_password


if __name__ == "__main__":

    print("Given a username and password, prints a hash string")
    username = input("Username:")
    password = input("Password:")
    print(hash_password(username, password))
