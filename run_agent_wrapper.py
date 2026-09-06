import subprocess
import time
import sys

def run_agent():
    # Start the agent process
    proc = subprocess.Popen([sys.executable, 'src/agent.py'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Wait for 10 seconds
    time.sleep(10)

    # Terminate the process
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    # Get the output
    stdout, stderr = proc.communicate()
    return stdout, stderr

if __name__ == '__main__':
    stdout, stderr = run_agent()
    print("STDOUT:")
    print(stdout)
    print("STDERR:")
    print(stderr)