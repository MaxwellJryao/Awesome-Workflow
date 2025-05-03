# SHELL
`zsh` is highly recommended as the shell used on a server, not only due to the various plugins supported by it, but also the clean and beautiful interfaces it provides - is there anyone who does not want 
a eyeable terminal ? :)

Next we introduce how to install and configure the `zsh` environment.

1. Download the `zsh` install file and install. Here we choose [oh-my-zsh](https://github.com/ohmyzsh/ohmyzsh).
   ```bash
   sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
   # or using wget
   # sh -c "$(wget -O- https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
   ```
2. Select using `zsh` as the default shell during the installation, or you may change the shell to `zsh` manually by
   ```bash
   chsh -s $(which zsh)
   ```
3. For some servers, using `chsh` to change the shell may not work due to authorization problems, etc., then one can simply add `zsh` to `~/.bashrc` to explicitly run `zsh` every time starting a new terminal.
4. After installing `zsh`, we define some alias and helpful functions in `example.sh`, which you may find useful and could be directly copied into your `.zshrc` configuration file.
5. Add some extra plugins for zsh.
   ```bash
   git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-autosuggestions
   git clone https://github.com/zsh-users/zsh-syntax-highlighting ${ZSH_CUSTOM:-~/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting

   # vim .zshrc
   plugins=(git zsh-syntax-highlighting zsh-autosuggestions)
   ```
6. Add tmux configuration.
   ```bash
   echo "set-option -g default-shell /usr/bin/zsh" > ~/.tmux.conf
   ```