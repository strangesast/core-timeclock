package config

// AMGRPCConfiguration holds xmlrpc connection configuration
type AMGRPCConfiguration struct {
	User     string
	Password string
	Host     string
	Port     int
}

// AMGMySQLConfiguration holds MySQL options
type AMGMySQLConfiguration struct {
	Host     string
	Database string
	User     string
	Password string
	Port     int
}

// DbConfiguration holds application db configuration
type DbConfiguration struct {
	Host     string
	Database string
	User     string
	Password string
	Port     int
}

// Configuration is base config for timeclock daemon
type Configuration struct {
	AMGRPCConfig   AMGRPCConfiguration   `mapstructure:"amg_rpc"`
	AMGMySQLConfig AMGMySQLConfiguration `mapstructure:"amg_mysql"`
	DbConfig       DbConfiguration       `mapstructure:"db"`
}
