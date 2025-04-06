# Git Utils
Install git-lfs without sudo privilege.
1. Browse the releases on [git-lfs](https://github.com/git-lfs/git-lfs/releases) and download the suitable one to local.
2. Extract the `.tar.gz` file.
3. Go into the directory
   ```bash
   cd git-lfs-x.x.x/
   ```
4. Change the path in `install.sh` from `/usr/local/` to `/path/to/your/local/dir/local`
5. Run the installation
   ```bash
   ./install.sh
   ```