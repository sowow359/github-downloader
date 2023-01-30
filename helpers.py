import time


def run_once_per(seconds):
    """
    Allows function to run again only after specified number of seconds.
    """

    last_run: float = float('-inf')

    def decorator(function):
        def wrapper(*args, **kwargs):
            nonlocal last_run

            passed = time.time() - last_run
            time.sleep(max(seconds - passed, 0))

            result = function(*args, **kwargs)
            last_run = time.time()
            return result

        return wrapper

    return decorator


def reporthook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if downloaded < total_size:
        print(int(downloaded / total_size * 100), '%', end="\r", sep='')
