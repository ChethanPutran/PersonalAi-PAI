source .venv/bin/activate
register-python-argcomplete server-cli > ~/.server-cli-complete
echo "source ~/.server-cli-complete" >> ~/.bashrc
activate-global-python-argcomplete --user