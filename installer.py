#!/usr/bin/env python3
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import archinstall
from archinstall import Installer
from archinstall import profile
from archinstall import SysInfo
from archinstall import mirrors
from archinstall import disk
from archinstall import menu
from archinstall import models
from archinstall import locale
from archinstall import info, debug

if TYPE_CHECKING:
	_: Any

install_paru = [
	'git clone https://aur.archlinux.org/paru.git',
	'cd paru',
	'makepkg -si --noconfirm'
]


def ask_user_questions():
	global_menu = archinstall.GlobalMenu(data_store=archinstall.arguments)

	global_menu.enable('archinstall-language')

	# Set which region to download packages from during the installation
	global_menu.enable('mirror_config')

	global_menu.enable('locale_config')

	global_menu.enable('disk_config', mandatory=True)

	# Specify disk encryption options
	global_menu.enable('disk_encryption')

	# Ask which boot-loader to use (will only ask if we're in UEFI mode, otherwise will default to GRUB)
	global_menu.enable('bootloader')

	# Get the hostname for the machine
	global_menu.enable('hostname')

	# Ask for a root password (optional, but triggers requirement for super-user if skipped)
	global_menu.enable('!root-password', mandatory=True)

	global_menu.enable('!users', mandatory=True)

	global_menu.enable('packages')

	global_menu.enable('parallel downloads')

	global_menu.enable('timezone')

	global_menu.enable('ntp')

	global_menu.enable('additional-repositories')

	global_menu.enable('__separator__')

	global_menu.enable('install')
	global_menu.enable('save_config')
	global_menu.enable('abort')

	global_menu.run()


def perform_installation(mountpoint: Path):
	"""
	Performs the installation steps on a block device.
	Only requirement is that the block devices are
	formatted and setup prior to entering this function.
	"""
	info('Starting installation')
	disk_config: disk.DiskLayoutConfiguration = archinstall.arguments['disk_config']

	# Retrieve list of additional repositories and set boolean values appropriately
	enable_testing = 'testing' in archinstall.arguments.get('additional-repositories', [])
	enable_multilib = 'multilib' in archinstall.arguments.get('additional-repositories', [])

	locale_config: locale.LocaleConfiguration = archinstall.arguments['locale_config']
	disk_encryption: disk.DiskEncryption = archinstall.arguments.get('disk_encryption', None)

	with Installer(
		mountpoint,
		disk_config,
		disk_encryption=disk_encryption,
		kernels=archinstall.arguments.get('kernels', ['linux-hardened'])
	) as installation:
		# Mount all the drives to the desired mountpoint
		if disk_config.config_type != disk.DiskLayoutType.Pre_mount:
			installation.mount_ordered_layout()

		installation.sanity_check()

		if disk_config.config_type != disk.DiskLayoutType.Pre_mount:
			if disk_encryption and disk_encryption.encryption_type != disk.EncryptionType.NoEncryption:
				# generate encryption key files for the mounted luks devices
				installation.generate_key_files()

		# Set mirrors used by pacstrap (outside of installation)
		if mirror_config := archinstall.arguments.get('mirror_config', None):
			if mirror_config.mirror_regions:
				mirrors.use_mirrors(mirror_config.mirror_regions)
			if mirror_config.custom_mirrors:
				mirrors.add_custom_mirrors(mirror_config.custom_mirrors)
		mirrors.add_custom_mirrors([
			mirrors.CustomMirror(
				'Blackarch',
				'https://www.blackarch.org/blackarch/$repo/os/$arch',
				sign_check=mirrors.SignCheck.Required,
				sign_option=mirrors.SignOption.TrustAll
			)
		])

		installation.minimal_installation(
			testing=enable_testing,
			multilib=enable_multilib,
			hostname=archinstall.arguments.get('hostname', 'archlinux'),
			locale_config=locale_config
		)

		if mirror_config := archinstall.arguments.get('mirror_config', None):
			installation.set_mirrors(mirror_config)  # Set the mirrors in the installation medium

		if archinstall.arguments.get('swap'):
			installation.setup_swap('zram')

		if archinstall.arguments.get("bootloader") == models.Bootloader.Grub and SysInfo.has_uefi():
			installation.add_additional_packages("grub")
		
		
		# Add packages:
		installation.add_additional_packages([
			'neovim',
			'git',
			'curl',
			'wget',
			'nmap',
			'gnu-netcat',
			'base-devel',
			'openssh',
			'python',
			'neofetch',
			'rust',
			'nodejs',
			'yarn',
			'npm',
			'go',
			'ufw',
			'python-pip',
			'blackarch-keyring',
			# Arch Server Profile Packages:
			'podman',
			'nginx',
			'mariadb',
			'postgresql',
			'cockpit',
			'cockpit-machines',
			'cockpit-podman',
			'cockpit-pcp',
			'cockpit-storaged',
		])
		installation.enable_service([
			'nginx',
			'mariadb',
			'postgresql',
			'cockpit',
			'sshd',
		])

		installation.add_bootloader(archinstall.arguments["bootloader"])

		# Use ISO network configuration
		installation.copy_iso_network_config()

		if users := archinstall.arguments.get('!users', None):
			installation.create_users(users)

		if archinstall.arguments.get('packages', None) and archinstall.arguments.get('packages', None)[0] != '':
			installation.add_additional_packages(archinstall.arguments.get('packages', []))

		if timezone := archinstall.arguments.get('timezone', None):
			installation.set_timezone(timezone)

		if archinstall.arguments.get('ntp', False):
			installation.activate_time_synchronization()

		if archinstall.accessibility_tools_in_use():
			installation.enable_espeakup()

		if (root_pw := archinstall.arguments.get('!root-password', None)) and len(root_pw):
			installation.user_set_pw('root', root_pw)

		# This step must be after profile installs to allow profiles_bck to install language pre-requisites.
		# After which, this step will set the language both for console and x11 if x11 was installed for instance.
		installation.set_keyboard_language(locale_config.kb_layout)

		# If the user provided a list of services to be enabled, pass the list to the enable_service function.
		# Note that while it's called enable_service, it can actually take a list of services and iterate it.
		if archinstall.arguments.get('services', None):
			installation.enable_service(archinstall.arguments.get('services', []))

		# If the user provided custom commands to be run post-installation, execute them now.
		archinstall.run_custom_user_commands(install_paru, installation)
		archinstall.run_custom_user_commands([
			'mariadb-install-db --user=mysql --basedir=/usr --datadir=/var/lib/mysql'
		], installation)
		for user in archinstall.arguments.get('!users', []):
			archinstall.run_custom_user_commands([
				f'usermod -a -G docker {user.username}'
			], installation)
		archinstall.run_custom_user_commands([
			'sudo -u postgres initdb -D /var/lib/postgres/data'
		], installation)
		if archinstall.arguments.get('custom-commands', None):
			archinstall.run_custom_user_commands(archinstall.arguments['custom-commands'], installation)

		installation.genfstab()

		info("For post-installation tips, see https://wiki.archlinux.org/index.php/Installation_guide#Post-installation")

		if not archinstall.arguments.get('silent'):
			prompt = str(_('Would you like to chroot into the newly created installation and perform post-installation configuration?'))
			choice = menu.Menu(prompt, menu.Menu.yes_no(), default_option=menu.Menu.yes()).run()
			if choice.value == menu.Menu.yes():
				try:
					installation.drop_to_shell()
				except Exception:
					pass

	debug(f"Disk states after installing: {disk.disk_layouts()}")


ask_user_questions()

fs_handler = disk.FilesystemHandler(
	archinstall.arguments['disk_config'],
	archinstall.arguments.get('disk_encryption', None)
)

fs_handler.perform_filesystem_operations()

perform_installation(archinstall.storage.get('MOUNT_POINT', Path('/mnt')))
